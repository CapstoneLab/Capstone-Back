"""Interactive CLI: pick a GitHub repository and branch via the running backend.

Usage: python select_repo.py [--base http://127.0.0.1:8000]

1) Waits for the JWT temp file written by /auth/github/callback.
2) GET /api/repos -> numbered list -> user picks a repo.
3) GET /api/repos/{owner}/{repo}/branches -> numbered list -> user picks a branch.
4) Prints the final selection and asks whether to pick another.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import time
from pathlib import Path

import httpx

JWT_TEMP_FILE = Path(tempfile.gettempdir()) / "cicd_last_jwt.txt"
WAIT_TIMEOUT_SECONDS = 180
POLL_INTERVAL = 1.0


def wait_for_jwt() -> str:
    """Poll the temp file until the login callback writes a fresh JWT."""
    try:
        if JWT_TEMP_FILE.exists():
            JWT_TEMP_FILE.unlink()
    except Exception:
        pass

    print(f"[select_repo] Waiting for GitHub login... (up to {WAIT_TIMEOUT_SECONDS}s)")
    print(f"[select_repo] Complete the login in the browser window that just opened.")
    deadline = time.time() + WAIT_TIMEOUT_SECONDS
    while time.time() < deadline:
        if JWT_TEMP_FILE.exists():
            token = JWT_TEMP_FILE.read_text(encoding="utf-8").strip()
            if token:
                print("[select_repo] Login detected. JWT loaded.")
                return token
        time.sleep(POLL_INTERVAL)

    print("[select_repo] Timed out waiting for login.")
    pasted = input("[select_repo] Paste JWT manually (or press Enter to quit): ").strip()
    if not pasted:
        sys.exit(1)
    return pasted


def fetch_repos(base: str, token: str) -> dict:
    r = httpx.get(
        f"{base}/api/repos",
        headers={"Authorization": f"Bearer {token}"},
        timeout=60.0,
    )
    r.raise_for_status()
    return r.json()


def fetch_whoami(base: str, token: str) -> dict:
    try:
        r = httpx.get(
            f"{base}/api/debug/whoami",
            headers={"Authorization": f"Bearer {token}"},
            timeout=30.0,
        )
        r.raise_for_status()
        return r.json()
    except Exception as exc:
        return {"error": str(exc)}


def fetch_branches(base: str, token: str, owner: str, repo: str) -> list[dict]:
    r = httpx.get(
        f"{base}/api/repos/{owner}/{repo}/branches",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30.0,
    )
    r.raise_for_status()
    data = r.json()
    return data.get("branches", [])


BACK = -1


def prompt_choice(items: list[str], label: str, allow_back: bool = False) -> int:
    hint = "b=back, q=quit" if allow_back else "q=quit"
    while True:
        raw = input(
            f"[select_repo] Pick a {label} [1-{len(items)}] ({hint}): "
        ).strip().lower()
        if raw in {"q", "quit", "exit"}:
            sys.exit(0)
        if allow_back and raw in {"b", "back"}:
            return BACK
        if raw.isdigit():
            idx = int(raw)
            if 1 <= idx <= len(items):
                return idx - 1
        print("  invalid input, try again")


def print_repos(repos: list[dict]) -> None:
    print()
    print(f"=== Repositories ({len(repos)}) ===")
    for i, r in enumerate(repos, 1):
        lock = "PRIVATE" if r.get("private") else "public "
        lang = r.get("language") or "-"
        print(
            f"  {i:>3}. [{lock}] {r.get('full_name'):<45}  "
            f"lang={lang:<10}  default={r.get('default_branch')}"
        )
    print()


def print_branches(owner: str, repo: str, branches: list[dict]) -> None:
    print()
    print(f"=== Branches of {owner}/{repo} ({len(branches)}) ===")
    for i, b in enumerate(branches, 1):
        prot = "protected" if b.get("protected") else "         "
        sha = (b.get("commit_sha") or "")[:7]
        print(f"  {i:>3}. {b.get('name'):<40}  {prot}  {sha}")
    print()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8000", help="backend base URL")
    args = parser.parse_args()
    base = args.base.rstrip("/")

    token = wait_for_jwt()

    whoami = fetch_whoami(base, token)
    scopes = whoami.get("scopes") or []
    orgs_whoami = whoami.get("orgs") or []
    print()
    if whoami.get("error"):
        print(f"[select_repo] whoami error  : {whoami['error']}")
    if whoami.get("scopes_error"):
        print(f"[select_repo] scopes error  : {whoami['scopes_error']}")
    if whoami.get("orgs_error"):
        print(f"[select_repo] orgs error    : {whoami['orgs_error']}")
    print(f"[select_repo] token scopes : {scopes or '(unknown)'}")
    print(
        f"[select_repo] orgs detected : "
        f"{[o.get('login') for o in orgs_whoami] or '(none — OAuth app may not be approved at org level)'}"
    )

    while True:
        try:
            data = fetch_repos(base, token)
        except httpx.HTTPStatusError as exc:
            print(f"[select_repo] /api/repos failed: {exc.response.status_code} {exc.response.text}")
            sys.exit(2)
        except httpx.RequestError as exc:
            print(f"[select_repo] network error: {exc}")
            sys.exit(2)

        repos = data.get("repos", [])
        orgs_seen = data.get("orgs_detected") or []
        org_errors = data.get("org_errors") or []
        if orgs_seen:
            print(f"[select_repo] merged orgs  : {orgs_seen}")
        if org_errors:
            print(f"[select_repo] org fetch errors: {org_errors}")

        if not repos:
            print("[select_repo] No accessible repositories.")
            sys.exit(0)

        print_repos(repos)
        idx = prompt_choice([r["full_name"] for r in repos], "repository")
        chosen = repos[idx]
        full_name = chosen["full_name"]
        owner, repo_name = full_name.split("/", 1)

        try:
            branches = fetch_branches(base, token, owner, repo_name)
        except httpx.HTTPStatusError as exc:
            print(f"[select_repo] branches failed: {exc.response.status_code} {exc.response.text}")
            continue

        if not branches:
            print(f"[select_repo] {full_name} has no branches.")
            continue

        print_branches(owner, repo_name, branches)
        b_idx = prompt_choice(
            [b["name"] for b in branches], "branch", allow_back=True
        )
        if b_idx == BACK:
            print("[select_repo] going back to repository list ...")
            continue
        chosen_branch = branches[b_idx]

        print()
        print("========================================")
        print(f"  repo   : {full_name}")
        print(f"  branch : {chosen_branch['name']}")
        print(f"  url    : {chosen.get('html_url')}")
        print(f"  sha    : {chosen_branch.get('commit_sha')}")
        print("========================================")
        print()

        again = input("[select_repo] Pick another? [y/N]: ").strip().lower()
        if again != "y":
            break

    print("[select_repo] done.")


if __name__ == "__main__":
    main()
