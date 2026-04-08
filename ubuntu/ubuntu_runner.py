#!/usr/bin/env python3
import argparse
import subprocess
import time
from datetime import datetime, timezone

import requests


def run_ci(repo_url: str, branch: str) -> tuple[str, list[str]]:
    logs: list[str] = []

    # Replace this with your real CI engine command.
    cmd = ["bash", "-lc", f"echo 'Running CI for {repo_url} ({branch})'; sleep 2; echo 'CI completed'"]
    proc = subprocess.run(cmd, capture_output=True, text=True)

    if proc.stdout:
        logs.extend(proc.stdout.strip().splitlines())
    if proc.stderr:
        logs.extend(proc.stderr.strip().splitlines())

    return ("success" if proc.returncode == 0 else "failed"), logs


def send_result(callback_url: str, callback_token: str, payload: dict) -> None:
    headers = {"x-callback-token": callback_token}
    last_exc: Exception | None = None
    for attempt in range(1, 6):
        try:
            response = requests.post(callback_url, json=payload, headers=headers, timeout=(3, 8))
            response.raise_for_status()
            return
        except requests.RequestException as exc:
            last_exc = exc
            if attempt == 5:
                raise
            time.sleep(min(2 ** (attempt - 1), 5))

    if last_exc is not None:
        raise last_exc


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--repo-url", dest="repo_url")
    parser.add_argument("--repo", dest="repo_url")
    parser.add_argument("--branch", default="main")
    parser.add_argument("--callback-url", required=True)
    parser.add_argument("--callback-token", required=True)
    args = parser.parse_args()

    if not args.repo_url:
        parser.error("one of --repo-url or --repo is required")

    started_at = datetime.now(timezone.utc)
    status, logs = run_ci(args.repo_url, args.branch)
    ended_at = datetime.now(timezone.utc)

    payload = {
        "job_id": args.job_id,
        "status": status,
        "repo_url": args.repo_url,
        "branch": args.branch,
        "started_at": started_at.isoformat(),
        "ended_at": ended_at.isoformat(),
        "logs": logs,
        "metadata": {"executor": "ubuntu-runner", "transport": "ssh-trigger/http-callback"},
    }

    send_result(args.callback_url, args.callback_token, payload)


if __name__ == "__main__":
    main()
