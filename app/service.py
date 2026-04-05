import json
import shlex
from pathlib import Path

import paramiko

from app.config import get_settings


class TriggerService:
    def __init__(self) -> None:
        self.settings = get_settings()

    def trigger(self, *, job_id: str, repo_url: str, branch: str) -> None:
        self._trigger_ssh(job_id=job_id, repo_url=repo_url, branch=branch)

    def _trigger_ssh(self, *, job_id: str, repo_url: str, branch: str) -> None:
        remote_cmd = self._build_remote_command(job_id=job_id, repo_url=repo_url, branch=branch)
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

        bg_cmd = f"nohup bash -lc {shlex.quote(remote_cmd)} >/tmp/ci_runner_{job_id}.log 2>&1 &"
        ssh.exec_command(f"bash -lc {shlex.quote(bg_cmd)}")
        ssh.close()

    def _build_remote_command(self, *, job_id: str, repo_url: str, branch: str) -> str:
        callback_url = f"{self.settings.windows_callback_base_url}/get-results"
        repo_arg_name = self.settings.ubuntu_runner_repo_arg.strip().lstrip("-") or "repo"

        runner_cmd = (
            f"{self.settings.ubuntu_python_command.strip() or 'python3'} "
            f"{shlex.quote(self.settings.ubuntu_runner_path)} "
            f"--job-id {shlex.quote(job_id)} "
            f"--{repo_arg_name} {shlex.quote(repo_url)} "
            f"--branch {shlex.quote(branch)} "
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
