import json
import shlex
from pathlib import Path

import paramiko

from app.config import get_settings


class TriggerService:
    def __init__(self) -> None:
        self.settings = get_settings()

    def trigger(self, *, job_id: str, repo_url: str, branch: str, env_vars: dict[str, str] | None = None) -> None:
        self._trigger_ssh(job_id=job_id, repo_url=repo_url, branch=branch, env_vars=env_vars)

    def _trigger_ssh(self, *, job_id: str, repo_url: str, branch: str, env_vars: dict[str, str] | None = None) -> None:
        remote_cmd = self._build_remote_command(job_id=job_id, repo_url=repo_url, branch=branch, env_vars=env_vars)
        print(f"[DEBUG] SSH connecting to {self.settings.ubuntu_ssh_host}:{self.settings.ubuntu_ssh_port} as {self.settings.ubuntu_ssh_user}")
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(
            hostname=self.settings.ubuntu_ssh_host,
            port=self.settings.ubuntu_ssh_port,
            username=self.settings.ubuntu_ssh_user,
            password=self.settings.ubuntu_ssh_password,
            timeout=self.settings.ssh_connect_timeout,
            look_for_keys=False,
            allow_agent=False,
        )

        log_file = f"/tmp/ci_runner_{job_id}.log"
        bg_cmd = (
            f"setsid nohup bash -lc {shlex.quote(remote_cmd)} "
            f">{shlex.quote(log_file)} 2>&1 </dev/null &"
        )
        print(f"[DEBUG SSH cmd] {bg_cmd}")

        try:
            _, stdout, stderr = ssh.exec_command(bg_cmd, timeout=10)
            exit_code = stdout.channel.recv_exit_status()
            err_out = stderr.read().decode("utf-8", errors="replace").strip()
            print(f"[DEBUG SSH exit_code] {exit_code}")
            if err_out:
                print(f"[DEBUG SSH stderr] {err_out}")
        finally:
            ssh.close()

    def _build_remote_command(self, *, job_id: str, repo_url: str, branch: str, env_vars: dict[str, str] | None = None) -> str:
        callback_url = f"{self.settings.windows_callback_base_url}/get-results"
        repo_arg_name = self.settings.ubuntu_runner_repo_arg.strip().lstrip("-") or "repo"

        runner_cmd = (
            f"{self.settings.ubuntu_python_command.strip() or 'python3'} "
            f"{shlex.quote(self.settings.ubuntu_runner_path)} "
            f"--job-id {shlex.quote(job_id)} "
            f"--{repo_arg_name} {shlex.quote(repo_url)} "
            f"--branch {shlex.quote(branch)} "
            f"--source capstone "
            f"--callback-url {shlex.quote(callback_url)} "
            f"--callback-token internal"
        )

        parts: list[str] = []
        working_dir = self.settings.ubuntu_working_dir.strip()
        if working_dir:
            parts.append(f"cd {shlex.quote(working_dir)}")

        prelude = self.settings.ubuntu_shell_prelude.strip()
        if prelude:
            parts.append(prelude)

        # 환경변수 export 추가
        if env_vars:
            for key, value in env_vars.items():
                parts.append(f"export {shlex.quote(key)}={shlex.quote(value)}")

        parts.append(runner_cmd)
        return " && ".join(parts)


class ResultStore:
    def __init__(self) -> None:
        settings = get_settings()
        self.base_dir = Path(settings.results_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def save(self, job_id: str, payload: dict) -> None:
        path = self.base_dir / f"{job_id}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def load(self, job_id: str) -> dict | None:
        path = self.base_dir / f"{job_id}.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def delete(self, job_id: str) -> None:
        path = self.base_dir / f"{job_id}.json"
        path.unlink(missing_ok=True)


class UbuntuResultFetcher:
    def __init__(self) -> None:
        self.settings = get_settings()

    def _connect_ssh(self) -> paramiko.SSHClient:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(
            hostname=self.settings.ubuntu_ssh_host,
            port=self.settings.ubuntu_ssh_port,
            username=self.settings.ubuntu_ssh_user,
            password=self.settings.ubuntu_ssh_password,
            timeout=5,
            look_for_keys=False,
            allow_agent=False,
        )
        return ssh

    def fetch_steps(self, run_id: str) -> list[dict] | None:
        if not run_id:
            return None

        working_dir = self.settings.ubuntu_working_dir.strip()
        if not working_dir:
            return None

        remote_path = f"{working_dir.rstrip('/')}/runs/{run_id}/pipeline_result.json"

        try:
            ssh = self._connect_ssh()

            sftp = ssh.open_sftp()
            with sftp.open(remote_path, "r") as fp:
                raw = fp.read()
            sftp.close()
            ssh.close()

            if isinstance(raw, bytes):
                raw = raw.decode("utf-8", "replace")

            obj = json.loads(raw)
            steps = obj.get("steps")
            if isinstance(steps, list):
                return [x for x in steps if isinstance(x, dict)]
            return None
        except Exception:
            return None

    def fetch_log_lines(self, job_id: str) -> list[str]:
        log_file = f"/tmp/ci_runner_{job_id}.log"
        try:
            ssh = self._connect_ssh()
            sftp = ssh.open_sftp()
            try:
                with sftp.open(log_file, "r") as fp:
                    raw = fp.read()
            except FileNotFoundError:
                sftp.close()
                ssh.close()
                return []
            sftp.close()
            ssh.close()

            if isinstance(raw, bytes):
                raw = raw.decode("utf-8", "replace")
            return raw.splitlines()
        except Exception:
            return []

    def kill_job(self, job_id: str) -> bool:
        """SSH로 접속해 job_id에 해당하는 프로세스를 kill. 성공 여부 반환."""
        try:
            ssh = self._connect_ssh()
            # job_id가 포함된 프로세스(python runner)를 찾아 SIGKILL
            kill_cmd = f"pkill -9 -f {shlex.quote(job_id)} 2>/dev/null; rm -f /tmp/ci_runner_{shlex.quote(job_id)}.log 2>/dev/null; true"
            _, stdout, _ = ssh.exec_command(kill_cmd, timeout=10)
            stdout.channel.recv_exit_status()
            ssh.close()
            print(f"[kill] sent SIGKILL for job_id={job_id}")
            return True
        except Exception as exc:
            print(f"[kill] failed to kill job_id={job_id}: {exc}")
            return False

    def fetch_result_by_job_id(self, job_id: str, scan_limit: int = 60) -> dict | None:
        if not job_id:
            return None

        working_dir = self.settings.ubuntu_working_dir.strip()
        if not working_dir:
            return None

        runs_dir = f"{working_dir.rstrip('/')}/runs"

        try:
            ssh = self._connect_ssh()
            sftp = ssh.open_sftp()

            try:
                entries = sftp.listdir_attr(runs_dir)
            except Exception:
                sftp.close()
                ssh.close()
                return None

            entries_sorted = sorted(entries, key=lambda x: x.st_mtime, reverse=True)
            for entry in entries_sorted[:scan_limit]:
                run_id = entry.filename
                remote_path = f"{runs_dir}/{run_id}/pipeline_result.json"

                try:
                    with sftp.open(remote_path, "r") as fp:
                        raw = fp.read()
                except Exception:
                    continue

                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8", "replace")

                try:
                    obj = json.loads(raw)
                except Exception:
                    continue

                metadata = obj.get("metadata")
                if not isinstance(metadata, dict):
                    continue

                if str(metadata.get("job_id", "")).strip() != job_id:
                    continue

                sftp.close()
                ssh.close()
                return obj if isinstance(obj, dict) else None

            sftp.close()
            ssh.close()
            return None
        except Exception:
            return None
