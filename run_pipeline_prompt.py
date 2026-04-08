import argparse
import json
import os
import re
import sys
import time
from urllib.parse import urlparse, urlunparse

import requests


def normalize_backend_url(raw_url: str) -> str:
    text = raw_url.strip().rstrip("/")
    parsed = urlparse(text)
    if parsed.scheme and parsed.hostname == "0.0.0.0":
        fixed_netloc = parsed.netloc.replace("0.0.0.0", "127.0.0.1", 1)
        fixed_url = urlunparse(parsed._replace(netloc=fixed_netloc)).rstrip("/")
        print(f"[info] backend-url 0.0.0.0 -> {fixed_url} 로 보정하여 요청합니다.")
        return fixed_url
    return text


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run pipeline from terminal prompt")
    parser.add_argument("--repo", default="", help="GitHub repository URL")
    parser.add_argument("--branch", default="main", help="Git branch")
    parser.add_argument("--backend-url", default="http://127.0.0.1:8000", help="Windows backend URL")
    parser.add_argument("--poll-interval", type=int, default=1, help="Polling interval in seconds")
    parser.add_argument(
        "--tail-lines",
        type=int,
        default=0,
        help="Number of log lines to print from the end (0 = print all)",
    )
    return parser.parse_args()


def ask_repo_if_needed(repo: str) -> str:
    if repo.strip():
        return repo.strip()

    value = input("파이썬 터미널 창에 깃허브 레포지토리를 입력하세요 : ").strip()
    if not value:
        raise ValueError("레포지토리 URL은 필수입니다")
    return value


def start_pipeline(base_url: str, repo: str, branch: str) -> str:
    response = requests.post(
        f"{base_url}/start-pipeline",
        json={"repo_url": repo, "branch": branch},
    )
    response.raise_for_status()
    body = response.json()
    return body["job_id"]


def poll_results(base_url: str, job_id: str, poll_interval: int) -> dict:
    start_ts = time.time()

    while True:
        elapsed = int(time.time() - start_ts)

        response = requests.get(
            f"{base_url}/get-results",
            params={"job_id": job_id},
        )
        response.raise_for_status()
        payload = response.json()

        found = payload.get("found", False)
        if found:
            print(f"[{elapsed:>3}s] 결과 수신 완료")
        else:
            print(f"[{elapsed:>3}s] 우분투에서 돌아가는 중입니다...")

        if found:
            return payload

        time.sleep(poll_interval)


def print_result(payload: dict, tail_lines: int) -> None:
    data = payload.get("data") or {}
    metadata = data.get("metadata") or {}
    logs = data.get("logs") or []
    if logs:
        if tail_lines > 0:
            selected = logs[-tail_lines:]
        else:
            selected = logs
        _print_ubuntu_style_logs(selected)

    _print_pipeline_result_summary(data, metadata)


def _print_ubuntu_style_logs(lines: list[str]) -> None:
    for line in lines:
        # Callback logs are prefixed with '[file.log] '. Strip only that prefix
        # so terminal output matches Ubuntu console style.
        if line.startswith("[") and "] " in line:
            print(line.split("] ", 1)[1])
        else:
            print(line)


def _print_pipeline_result_summary(data: dict, metadata: dict) -> None:
    run_id = metadata.get("run_id", "")
    status = data.get("status", "unknown")
    working_dir = os.getenv("UBUNTU_WORKING_DIR", "").strip()
    result_file = ""
    if run_id and working_dir:
        result_file = f"{working_dir.rstrip('/')}/runs/{run_id}/pipeline_result.json"

    print("\n=== Pipeline Result ===")
    print(f"run_id: {run_id or 'unknown'}")
    print(f"status: {status}")
    if result_file:
        print(f"result file: {result_file}")

    exact_steps = data.get("steps")
    if not isinstance(exact_steps, list) or not exact_steps:
        exact_steps = _load_steps_from_ubuntu(run_id=run_id)
    if exact_steps is not None:
        has_skipped = False
        for step in exact_steps:
            step_name = (
                step.get("step_name")
                or step.get("name")
                or step.get("step")
                or "unknown"
            )
            step_status = step.get("status", "unknown")
            summary = step.get("summary_message") or step.get("summary") or "no message"
            if step_status == "skipped":
                has_skipped = True
            print(f"- {step_name}: {step_status} ({summary})")
        if has_skipped and status == "success":
            print("note: overall pipeline status can be success even when some steps are skipped")
        return

    step_map = {
        "clone.log": "clone",
        "install.log": "install",
        "lightweight-security.log": "lightweight-security",
        "test.log": "test",
        "deep-security.log": "deep-security",
        "build.log": "build",
    }
    step_msg_success = {
        "clone": "Repository cloned and branch checked out",
        "install": "dependencies installed",
        "lightweight-security": "gitleaks completed",
        "test": "test command completed",
        "deep-security": "semgrep completed",
        "build": "build command completed",
    }

    logs = data.get("logs") or []
    per_file: dict[str, list[str]] = {}
    for line in logs:
        m = re.match(r"^\[(?P<file>[^\]]+)\]\s(?P<msg>.*)$", line)
        if not m:
            continue
        file_name = m.group("file")
        message = m.group("msg")
        per_file.setdefault(file_name, []).append(message)

    has_skipped = False
    for file_name, step_name in step_map.items():
        if file_name not in per_file:
            continue

        exit_code = None
        no_tests_detected = False
        for msg in per_file[file_name]:
            m = re.search(r"\[exit_code\]\s(-?\d+)", msg)
            if m:
                exit_code = int(m.group(1))
            if step_name == "test" and "No tests found" in msg:
                no_tests_detected = True

        if step_name == "test" and no_tests_detected:
            step_status = "skipped"
            summary = "No tests found; skipped"
            has_skipped = True
        elif exit_code is None:
            step_status = "pending"
            summary = "no exit_code found"
        elif exit_code == 0:
            step_status = "success"
            summary = step_msg_success.get(step_name, "completed")
        else:
            step_status = "failed"
            summary = f"exit_code={exit_code}"

        print(f"- {step_name}: {step_status} ({summary})")

    if has_skipped and status == "success":
        print("note: overall pipeline status can be success even when some steps are skipped")


def _load_steps_from_ubuntu(run_id: str) -> list[dict] | None:
    if not run_id:
        return None

    host = os.getenv("UBUNTU_SSH_HOST", "").strip()
    port = int(os.getenv("UBUNTU_SSH_PORT", "22").strip() or "22")
    user = os.getenv("UBUNTU_SSH_USER", "").strip()
    password = os.getenv("UBUNTU_SSH_PASSWORD", "").strip()
    working_dir = os.getenv("UBUNTU_WORKING_DIR", "").strip()

    if not (host and user and password and working_dir):
        return None

    try:
        import paramiko  # type: ignore
    except Exception:
        return None

    remote_path = f"{working_dir.rstrip('/')}/runs/{run_id}/pipeline_result.json"

    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(
            hostname=host,
            port=port,
            username=user,
            password=password,
            timeout=5,
            look_for_keys=False,
            allow_agent=False,
        )

        sftp = ssh.open_sftp()
        with sftp.open(remote_path, "r") as fp:
            content = fp.read()
        sftp.close()
        ssh.close()

        if isinstance(content, bytes):
            content = content.decode("utf-8", "replace")

        obj = json.loads(content)
        steps = obj.get("steps")
        if isinstance(steps, list):
            return [x for x in steps if isinstance(x, dict)]
        return None
    except BaseException:
        return None


def main() -> int:
    try:
        args = parse_args()
        repo = ask_repo_if_needed(args.repo)
        backend_base_url = normalize_backend_url(args.backend_url)

        print("파이프라인 시작 요청 중...")
        job_id = start_pipeline(backend_base_url, repo, args.branch)
        print("job_id:", job_id)

        print("우분투에서 파이프라인이 실행 중입니다. 결과를 기다리는 중...")
        result = poll_results(
            base_url=backend_base_url,
            job_id=job_id,
            poll_interval=args.poll_interval,
        )

        print_result(result, tail_lines=args.tail_lines)
        return 0
    except requests.HTTPError as exc:
        detail = ""
        if exc.response is not None:
            detail = f" status={exc.response.status_code}, body={exc.response.text}"
        print(f"HTTP 오류:{detail}")
        return 2
    except Exception as exc:
        print(f"오류: {exc}")
        return 3


if __name__ == "__main__":
    sys.exit(main())
