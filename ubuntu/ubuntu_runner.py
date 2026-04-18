#!/usr/bin/env python3
"""
CI/CD Pipeline Runner
─────────────────────
Supports: Java (Gradle/Maven), Python (pip/poetry), Node.js (npm/yarn/pnpm)

Steps: clone → detect → install → test → security-scan → build → callback
"""

import argparse
import json
import os
import shutil
import subprocess

import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests

# ── Constants ──────────────────────────────────────────────────────────────

WORKSPACE = Path("/tmp/ci-workspace")
RUNS_DIR = Path.home() / "ci-runs"


# ── Project Type Detection ─────────────────────────────────────────────────

def detect_project_type(project_dir: Path) -> str:
    """Detect the project type based on manifest files."""
    markers = {
        "java-gradle": ["build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts"],
        "java-maven": ["pom.xml"],
        "python": ["requirements.txt", "pyproject.toml", "setup.py", "Pipfile"],
        "node": ["package.json"],
    }
    for ptype, files in markers.items():
        for f in files:
            if (project_dir / f).exists():
                return ptype
    return "unknown"


# ── Step Runner ────────────────────────────────────────────────────────────

class StepResult:
    def __init__(self, name: str, step_type: str):
        self.name = name
        self.step_type = step_type
        self.status = "pending"
        self.started_at: Optional[str] = None
        self.ended_at: Optional[str] = None
        self.duration_secs: float = 0
        self.exit_code: Optional[int] = None
        self.logs: list[str] = []
        self.error_message: Optional[str] = None
        self.metadata: dict = {}

    def to_dict(self) -> dict:
        return {
            "step_name": self.name,
            "step_type": self.step_type,
            "status": self.status,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "duration_secs": round(self.duration_secs, 2),
            "exit_code": self.exit_code,
            "logs": self.logs,
            "error_message": self.error_message,
            "metadata": self.metadata,
        }


def run_command(cmd: list[str] | str, cwd: Path, step: StepResult,
                timeout: int = 600, env: Optional[dict] = None) -> int:
    """Execute a shell command, capture output, update step result."""
    step.status = "running"
    step.started_at = datetime.now(timezone.utc).isoformat()
    step.logs.append(f"$ {cmd if isinstance(cmd, str) else ' '.join(cmd)}")

    run_env = {**os.environ, **(env or {})}
    shell = isinstance(cmd, str)

    try:
        proc = subprocess.run(
            cmd, cwd=str(cwd), capture_output=True, text=True,
            timeout=timeout, shell=shell, env=run_env,
        )
        if proc.stdout:
            step.logs.extend(proc.stdout.strip().splitlines())
        if proc.stderr:
            step.logs.extend(proc.stderr.strip().splitlines())
        step.exit_code = proc.returncode
        step.status = "success" if proc.returncode == 0 else "failed"
        if proc.returncode != 0:
            step.error_message = f"Exit code {proc.returncode}"
    except subprocess.TimeoutExpired:
        step.exit_code = -1
        step.status = "failed"
        step.error_message = f"Timeout after {timeout}s"
        step.logs.append(f"[ERROR] Command timed out after {timeout}s")
    except Exception as e:
        step.exit_code = -1
        step.status = "failed"
        step.error_message = str(e)
        step.logs.append(f"[ERROR] {e}")

    step.ended_at = datetime.now(timezone.utc).isoformat()
    start = datetime.fromisoformat(step.started_at)
    end = datetime.fromisoformat(step.ended_at)
    step.duration_secs = (end - start).total_seconds()
    return step.exit_code or 0


# ── Pipeline Steps ─────────────────────────────────────────────────────────

def step_clone(repo_url: str, branch: str, workspace: Path) -> StepResult:
    """Clone the repository."""
    step = StepResult("clone", "clone")

    if workspace.exists():
        shutil.rmtree(workspace, ignore_errors=True)
    workspace.mkdir(parents=True, exist_ok=True)

    rc = run_command(
        ["git", "clone", "--depth", "1", "--branch", branch, repo_url, str(workspace)],
        cwd=workspace.parent, step=step, timeout=120,
    )

    # Fallback: try main/master if branch not found
    if rc != 0 and branch not in ("main", "master"):
        step.logs.append(f"[WARN] Branch '{branch}' failed, trying 'main'...")
        step.status = "pending"
        shutil.rmtree(workspace, ignore_errors=True)
        workspace.mkdir(parents=True, exist_ok=True)
        rc = run_command(
            ["git", "clone", "--depth", "1", "--branch", "main", repo_url, str(workspace)],
            cwd=workspace.parent, step=step, timeout=120,
        )
        if rc != 0:
            step.logs.append("[WARN] 'main' failed, trying 'master'...")
            step.status = "pending"
            shutil.rmtree(workspace, ignore_errors=True)
            workspace.mkdir(parents=True, exist_ok=True)
            run_command(
                ["git", "clone", "--depth", "1", "--branch", "master", repo_url, str(workspace)],
                cwd=workspace.parent, step=step, timeout=120,
            )

    return step


def step_install(project_dir: Path, project_type: str) -> StepResult:
    """Install dependencies based on project type."""
    step = StepResult("install", "install")
    step.metadata["project_type"] = project_type

    if project_type == "java-gradle":
        # Gradle wrapper or system gradle
        gradle_cmd = "./gradlew" if (project_dir / "gradlew").exists() else "gradle"
        if (project_dir / "gradlew").exists():
            run_command(["chmod", "+x", "gradlew"], cwd=project_dir, step=step)
            step.logs.append("[INFO] Using Gradle Wrapper (gradlew)")
        run_command(
            [gradle_cmd, "dependencies", "--no-daemon"],
            cwd=project_dir, step=step, timeout=300,
        )

    elif project_type == "java-maven":
        mvn_cmd = "./mvnw" if (project_dir / "mvnw").exists() else "mvn"
        if (project_dir / "mvnw").exists():
            run_command(["chmod", "+x", "mvnw"], cwd=project_dir, step=step)
            step.logs.append("[INFO] Using Maven Wrapper (mvnw)")
        run_command(
            [mvn_cmd, "dependency:resolve", "-q"],
            cwd=project_dir, step=step, timeout=300,
        )

    elif project_type == "python":
        if (project_dir / "Pipfile").exists():
            run_command(["pipenv", "install", "--dev"], cwd=project_dir, step=step, timeout=300)
        elif (project_dir / "pyproject.toml").exists():
            # Check if it's a poetry project
            toml_text = (project_dir / "pyproject.toml").read_text(errors="replace")
            if "tool.poetry" in toml_text:
                run_command(["poetry", "install"], cwd=project_dir, step=step, timeout=300)
            else:
                run_command(["pip", "install", "-e", ".[dev]"], cwd=project_dir, step=step, timeout=300)
        elif (project_dir / "requirements.txt").exists():
            run_command(["pip", "install", "-r", "requirements.txt"], cwd=project_dir, step=step, timeout=300)
            # Also install dev requirements if available
            if (project_dir / "requirements-dev.txt").exists():
                run_command(["pip", "install", "-r", "requirements-dev.txt"], cwd=project_dir, step=step, timeout=300)

    elif project_type == "node":
        lock_yarn = (project_dir / "yarn.lock").exists()
        lock_pnpm = (project_dir / "pnpm-lock.yaml").exists()

        if lock_pnpm:
            run_command(["pnpm", "install", "--frozen-lockfile"], cwd=project_dir, step=step, timeout=300)
        elif lock_yarn:
            run_command(["yarn", "install", "--frozen-lockfile"], cwd=project_dir, step=step, timeout=300)
        else:
            run_command(["npm", "ci"], cwd=project_dir, step=step, timeout=300)

    else:
        step.status = "skipped"
        step.logs.append("[SKIP] Unknown project type — no install step")

    return step


def step_test(project_dir: Path, project_type: str) -> StepResult:
    """Run tests based on project type."""
    step = StepResult("test", "test")
    step.metadata["project_type"] = project_type

    if project_type == "java-gradle":
        gradle_cmd = "./gradlew" if (project_dir / "gradlew").exists() else "gradle"
        run_command(
            [gradle_cmd, "test", "--no-daemon"],
            cwd=project_dir, step=step, timeout=600,
        )
        # Collect test report summary
        report_dir = project_dir / "build" / "reports" / "tests" / "test"
        if report_dir.exists():
            step.metadata["test_report_path"] = str(report_dir)
        # Collect test results XML
        results_dir = project_dir / "build" / "test-results" / "test"
        if results_dir.exists():
            xml_files = list(results_dir.glob("*.xml"))
            step.metadata["test_result_files"] = len(xml_files)

    elif project_type == "java-maven":
        mvn_cmd = "./mvnw" if (project_dir / "mvnw").exists() else "mvn"
        run_command(
            [mvn_cmd, "test", "-B"],
            cwd=project_dir, step=step, timeout=600,
        )
        surefire_dir = project_dir / "target" / "surefire-reports"
        if surefire_dir.exists():
            xml_files = list(surefire_dir.glob("*.xml"))
            step.metadata["test_result_files"] = len(xml_files)

    elif project_type == "python":
        # Try pytest first, then unittest
        if (project_dir / "pytest.ini").exists() or \
           (project_dir / "setup.cfg").exists() or \
           (project_dir / "pyproject.toml").exists() or \
           list(project_dir.rglob("test_*.py")) or \
           list(project_dir.rglob("*_test.py")):
            run_command(
                ["python", "-m", "pytest", "-v", "--tb=short"],
                cwd=project_dir, step=step, timeout=300,
            )
        else:
            step.status = "skipped"
            step.logs.append("[SKIP] No test files found")

    elif project_type == "node":
        pkg_json = project_dir / "package.json"
        if pkg_json.exists():
            pkg_data = json.loads(pkg_json.read_text(errors="replace"))
            scripts = pkg_data.get("scripts", {})
            if "test" in scripts and scripts["test"] != 'echo "Error: no test specified" && exit 1':
                lock_pnpm = (project_dir / "pnpm-lock.yaml").exists()
                lock_yarn = (project_dir / "yarn.lock").exists()
                if lock_pnpm:
                    run_command(["pnpm", "test"], cwd=project_dir, step=step, timeout=300)
                elif lock_yarn:
                    run_command(["yarn", "test"], cwd=project_dir, step=step, timeout=300)
                else:
                    run_command(["npm", "test"], cwd=project_dir, step=step, timeout=300)
            else:
                step.status = "skipped"
                step.logs.append("[SKIP] No test script defined in package.json")
        else:
            step.status = "skipped"
            step.logs.append("[SKIP] No package.json found")

    else:
        step.status = "skipped"
        step.logs.append("[SKIP] Unknown project type — no test step")

    return step


def step_security_gitleaks(project_dir: Path) -> StepResult:
    """Run gitleaks to detect hardcoded secrets."""
    step = StepResult("lightweight-security", "security")
    step.metadata["tool"] = "gitleaks"

    report_path = project_dir / "gitleaks-report.json"
    run_command(
        ["gitleaks", "detect", "--source", ".", "--report-format", "json",
         "--report-path", str(report_path), "--no-git"],
        cwd=project_dir, step=step, timeout=120,
    )

    # gitleaks exit code 1 = findings, 0 = clean
    if report_path.exists():
        try:
            findings = json.loads(report_path.read_text(errors="replace"))
            if isinstance(findings, list):
                step.metadata["findings_count"] = len(findings)
                step.metadata["findings"] = findings[:50]  # limit
        except json.JSONDecodeError:
            pass

    # gitleaks returns exit code 1 when it finds secrets — this is not a runner failure
    if step.exit_code == 1:
        step.status = "success"
        step.logs.append(f"[WARN] Gitleaks found {step.metadata.get('findings_count', 0)} secret(s)")

    return step


def step_security_semgrep(project_dir: Path) -> StepResult:
    """Run semgrep for code vulnerability scanning."""
    step = StepResult("deep-security", "security")
    step.metadata["tool"] = "semgrep"

    report_path = project_dir / "semgrep-report.json"
    run_command(
        ["semgrep", "--config", "auto", "--json", "--output", str(report_path), "."],
        cwd=project_dir, step=step, timeout=300,
    )

    if report_path.exists():
        try:
            report = json.loads(report_path.read_text(errors="replace"))
            results = report.get("results", [])
            step.metadata["findings_count"] = len(results)
            step.metadata["rules_run"] = len(report.get("paths", {}).get("scanned", []))
            # Summarize by severity
            severity_counts = {}
            for r in results:
                sev = r.get("extra", {}).get("severity", "UNKNOWN")
                severity_counts[sev] = severity_counts.get(sev, 0) + 1
            step.metadata["severity_summary"] = severity_counts
            step.metadata["findings"] = [
                {
                    "rule_id": r.get("check_id", ""),
                    "severity": r.get("extra", {}).get("severity", ""),
                    "message": r.get("extra", {}).get("message", "")[:200],
                    "file": r.get("path", ""),
                    "line": r.get("start", {}).get("line", 0),
                }
                for r in results[:50]
            ]
        except json.JSONDecodeError:
            pass

    return step


def step_build(project_dir: Path, project_type: str) -> StepResult:
    """Build the project."""
    step = StepResult("build", "build")
    step.metadata["project_type"] = project_type

    if project_type == "java-gradle":
        gradle_cmd = "./gradlew" if (project_dir / "gradlew").exists() else "gradle"
        run_command(
            [gradle_cmd, "build", "-x", "test", "--no-daemon"],
            cwd=project_dir, step=step, timeout=600,
        )
        # Check for built artifacts
        libs_dir = project_dir / "build" / "libs"
        if libs_dir.exists():
            jars = list(libs_dir.glob("*.jar"))
            step.metadata["artifacts"] = [j.name for j in jars]
            step.metadata["artifact_count"] = len(jars)

    elif project_type == "java-maven":
        mvn_cmd = "./mvnw" if (project_dir / "mvnw").exists() else "mvn"
        run_command(
            [mvn_cmd, "package", "-DskipTests", "-B"],
            cwd=project_dir, step=step, timeout=600,
        )
        target_dir = project_dir / "target"
        if target_dir.exists():
            jars = list(target_dir.glob("*.jar"))
            wars = list(target_dir.glob("*.war"))
            step.metadata["artifacts"] = [a.name for a in jars + wars]
            step.metadata["artifact_count"] = len(jars) + len(wars)

    elif project_type == "python":
        # Python typically doesn't have a build step, but check for setup.py/pyproject.toml
        if (project_dir / "setup.py").exists():
            run_command(["python", "setup.py", "bdist_wheel"], cwd=project_dir, step=step, timeout=120)
        elif (project_dir / "pyproject.toml").exists():
            run_command(["python", "-m", "build"], cwd=project_dir, step=step, timeout=120)
        else:
            step.status = "skipped"
            step.logs.append("[SKIP] No build configuration found (not required for most Python projects)")

    elif project_type == "node":
        pkg_json = project_dir / "package.json"
        if pkg_json.exists():
            pkg_data = json.loads(pkg_json.read_text(errors="replace"))
            scripts = pkg_data.get("scripts", {})
            if "build" in scripts:
                lock_pnpm = (project_dir / "pnpm-lock.yaml").exists()
                lock_yarn = (project_dir / "yarn.lock").exists()
                if lock_pnpm:
                    run_command(["pnpm", "run", "build"], cwd=project_dir, step=step, timeout=300)
                elif lock_yarn:
                    run_command(["yarn", "build"], cwd=project_dir, step=step, timeout=300)
                else:
                    run_command(["npm", "run", "build"], cwd=project_dir, step=step, timeout=300)
            else:
                step.status = "skipped"
                step.logs.append("[SKIP] No build script in package.json")

    else:
        step.status = "skipped"
        step.logs.append("[SKIP] Unknown project type — no build step")

    return step


# ── Dependency Check (Java-specific) ──────────────────────────────────────

def step_dependency_check(project_dir: Path, project_type: str) -> StepResult:
    """Check for known vulnerable dependencies (Java: OWASP, Node: npm audit, Python: pip-audit)."""
    step = StepResult("dependency-check", "security")
    step.metadata["project_type"] = project_type

    if project_type == "java-gradle":
        # Try gradle dependency insight for common vulnerable libraries
        gradle_cmd = "./gradlew" if (project_dir / "gradlew").exists() else "gradle"
        run_command(
            [gradle_cmd, "dependencies", "--configuration", "runtimeClasspath", "--no-daemon"],
            cwd=project_dir, step=step, timeout=120,
        )
        # Parse dependency tree for known problematic libraries
        _check_java_deps(step, project_dir)

    elif project_type == "java-maven":
        mvn_cmd = "./mvnw" if (project_dir / "mvnw").exists() else "mvn"
        run_command(
            [mvn_cmd, "dependency:tree", "-B"],
            cwd=project_dir, step=step, timeout=120,
        )
        _check_java_deps(step, project_dir)

    elif project_type == "node":
        run_command(["npm", "audit", "--json"], cwd=project_dir, step=step, timeout=60)
        if step.exit_code and step.exit_code > 0:
            # npm audit exit code > 0 means vulnerabilities found, not a runner error
            step.status = "success"
            step.logs.append("[WARN] npm audit found vulnerabilities")

    elif project_type == "python":
        # Try pip-audit if available
        run_command(["pip-audit", "--format", "json"], cwd=project_dir, step=step, timeout=120)
        if step.exit_code == 127 or "not found" in " ".join(step.logs).lower():
            step.status = "skipped"
            step.logs.append("[SKIP] pip-audit not installed")

    else:
        step.status = "skipped"
        step.logs.append("[SKIP] No dependency check for this project type")

    return step


def _check_java_deps(step: StepResult, _project_dir: Path) -> None:
    """Analyze Java dependency output for known problematic libraries."""
    log_text = "\n".join(step.logs)
    warnings = []

    # Known vulnerable library patterns
    risky_patterns = {
        "log4j-core:2.0": "CVE-2021-44228 (Log4Shell) — upgrade to 2.17.1+",
        "log4j-core:2.1": "CVE-2021-44228 (Log4Shell) — upgrade to 2.17.1+",
        "log4j-core:2.2": "CVE-2021-44228 (Log4Shell) — upgrade to 2.17.1+",
        "log4j-core:2.3": "CVE-2021-44228 (Log4Shell) — upgrade to 2.17.1+",
        "log4j-core:2.4": "CVE-2021-44228 (Log4Shell) — upgrade to 2.17.1+",
        "log4j-core:2.5": "CVE-2021-44228 (Log4Shell) — upgrade to 2.17.1+",
        "log4j-core:2.6": "CVE-2021-44228 (Log4Shell) — upgrade to 2.17.1+",
        "log4j-core:2.7": "CVE-2021-44228 (Log4Shell) — upgrade to 2.17.1+",
        "log4j-core:2.8": "CVE-2021-44228 (Log4Shell) — upgrade to 2.17.1+",
        "log4j-core:2.9": "CVE-2021-44228 (Log4Shell) — upgrade to 2.17.1+",
        "log4j-core:2.10": "CVE-2021-44228 (Log4Shell) — upgrade to 2.17.1+",
        "log4j-core:2.11": "CVE-2021-44228 (Log4Shell) — upgrade to 2.17.1+",
        "log4j-core:2.12": "CVE-2021-44228 (Log4Shell) — upgrade to 2.17.1+",
        "log4j-core:2.13": "CVE-2021-44228 (Log4Shell) — upgrade to 2.17.1+",
        "log4j-core:2.14": "CVE-2021-44228 (Log4Shell) — upgrade to 2.17.1+",
        "commons-collections:3.": "Deserialization RCE — upgrade to 3.2.2+ or use 4.x",
        "spring-core:4.": "EOL — upgrade to Spring 5.3+ or 6.x",
        "jackson-databind:2.6": "CVE-2019-12086 — upgrade to 2.13+",
        "jackson-databind:2.7": "CVE-2019-12086 — upgrade to 2.13+",
        "jackson-databind:2.8": "CVE-2019-12086 — upgrade to 2.13+",
        "jackson-databind:2.9": "CVE-2019-12086 — upgrade to 2.13+",
        "fastjson:1.2": "Multiple RCE — migrate to fastjson2 or Jackson",
        "struts2-core:2.": "CVE-2017-5638 — upgrade or migrate",
        "commons-fileupload:1.3": "CVE-2023-24998 — upgrade to 1.5+",
        "snakeyaml:1.": "CVE-2022-1471 — upgrade to 2.0+",
        "gson:2.8": "DoS vulnerability — upgrade to 2.10+",
        "h2:1.": "CVE-2021-42392 — upgrade to 2.1+",
        "tomcat-embed-core:8.": "Multiple CVEs — upgrade to 9.0.70+",
        "tomcat-embed-core:9.0.0": "Multiple CVEs — upgrade to 9.0.70+",
    }

    for pattern, warning in risky_patterns.items():
        if pattern.lower() in log_text.lower():
            warnings.append(f"[WARN] Risky dependency detected: {pattern} → {warning}")

    if warnings:
        step.logs.extend(warnings)
        step.metadata["dependency_warnings"] = warnings
        step.metadata["risky_deps_count"] = len(warnings)
    else:
        step.logs.append("[OK] No known risky dependencies detected")


# ── Main Pipeline ──────────────────────────────────────────────────────────

def run_pipeline(job_id: str, repo_url: str, branch: str) -> dict:
    """Run the full CI/CD pipeline and return results."""
    pipeline_start = datetime.now(timezone.utc)
    workspace = WORKSPACE / job_id
    steps: list[dict] = []
    overall_status = "success"

    print(f"[PIPELINE] Starting job {job_id}")
    print(f"[PIPELINE] Repo: {repo_url} Branch: {branch}")

    # ── Step 1: Clone ──
    print("[STEP] clone")
    result = step_clone(repo_url, branch, workspace)
    steps.append(result.to_dict())
    if result.status == "failed":
        overall_status = "failed"
        return _build_result(job_id, repo_url, branch, steps, overall_status, pipeline_start)

    # ── Step 2: Detect Project Type ──
    project_type = detect_project_type(workspace)
    print(f"[DETECT] Project type: {project_type}")

    # ── Step 3: Install ──
    print("[STEP] install")
    result = step_install(workspace, project_type)
    steps.append(result.to_dict())
    if result.status == "failed":
        overall_status = "failed"

    # ── Step 4: Test ──
    print("[STEP] test")
    result = step_test(workspace, project_type)
    steps.append(result.to_dict())
    if result.status == "failed":
        overall_status = "failed"

    # ── Step 5: Dependency Check ──
    print("[STEP] dependency-check")
    result = step_dependency_check(workspace, project_type)
    steps.append(result.to_dict())

    # ── Step 6: Gitleaks ──
    print("[STEP] lightweight-security (gitleaks)")
    result = step_security_gitleaks(workspace)
    steps.append(result.to_dict())

    # ── Step 7: Semgrep ──
    print("[STEP] deep-security (semgrep)")
    result = step_security_semgrep(workspace)
    steps.append(result.to_dict())

    # ── Step 8: Build ──
    print("[STEP] build")
    result = step_build(workspace, project_type)
    steps.append(result.to_dict())
    if result.status == "failed":
        overall_status = "failed"

    return _build_result(job_id, repo_url, branch, steps, overall_status, pipeline_start, project_type)


def _build_result(job_id: str, repo_url: str, branch: str,
                  steps: list[dict], overall_status: str,
                  pipeline_start: datetime,
                  project_type: str = "unknown") -> dict:
    """Build the final pipeline result payload."""
    pipeline_end = datetime.now(timezone.utc)
    duration = (pipeline_end - pipeline_start).total_seconds()

    return {
        "job_id": job_id,
        "status": overall_status,
        "repo_url": repo_url,
        "branch": branch,
        "project_type": project_type,
        "started_at": pipeline_start.isoformat(),
        "ended_at": pipeline_end.isoformat(),
        "duration_secs": round(duration, 2),
        "steps": steps,
        "metadata": {
            "job_id": job_id,
            "executor": "ubuntu-runner",
            "transport": "ssh-trigger/http-callback",
            "project_type": project_type,
        },
    }


# ── Result Saving & Callback ──────────────────────────────────────────────

def save_result(job_id: str, result: dict) -> None:
    """Save pipeline result to disk for SFTP retrieval."""
    run_dir = RUNS_DIR / job_id
    run_dir.mkdir(parents=True, exist_ok=True)
    result_path = run_dir / "pipeline_result.json"
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[SAVE] Result saved to {result_path}")


def send_result(callback_url: str, callback_token: str, payload: dict) -> None:
    """Send pipeline result to the Windows callback endpoint."""
    headers = {"x-callback-token": callback_token}
    for attempt in range(1, 6):
        try:
            response = requests.post(callback_url, json=payload, headers=headers, timeout=(3, 15))
            response.raise_for_status()
            print(f"[CALLBACK] Success (attempt {attempt})")
            return
        except requests.RequestException as exc:
            print(f"[CALLBACK] Attempt {attempt} failed: {exc}")
            if attempt == 5:
                print("[CALLBACK] All attempts failed")
                raise
            time.sleep(min(2 ** (attempt - 1), 5))


# ── Entry Point ────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="CI/CD Pipeline Runner")
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--repo-url", dest="repo_url")
    parser.add_argument("--repo", dest="repo_url")
    parser.add_argument("--branch", default="main")
    parser.add_argument("--callback-url", required=True)
    parser.add_argument("--callback-token", required=True)
    args = parser.parse_args()

    if not args.repo_url:
        parser.error("one of --repo-url or --repo is required")

    result = run_pipeline(args.job_id, args.repo_url, args.branch)

    # Save result locally for SFTP access
    save_result(args.job_id, result)

    # Send to callback
    send_result(args.callback_url, args.callback_token, result)


if __name__ == "__main__":
    main()
