from typing import Any

import httpx

GITHUB_API_BASE = "https://api.github.com"
_TIMEOUT = httpx.Timeout(15.0, connect=10.0)
_PER_PAGE = 100
_MAX_PAGES = 10


def _headers(access_token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "cicd-backend",
    }


async def list_user_repos(access_token: str) -> list[dict[str, Any]]:
    """Fetch all repos the authenticated user can access (owner + collaborator)."""
    repos: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        for page in range(1, _MAX_PAGES + 1):
            r = await client.get(
                f"{GITHUB_API_BASE}/user/repos",
                headers=_headers(access_token),
                params={
                    "per_page": _PER_PAGE,
                    "page": page,
                    "sort": "updated",
                    "affiliation": "owner,collaborator,organization_member",
                },
            )
            r.raise_for_status()
            chunk = r.json()
            if not isinstance(chunk, list) or not chunk:
                break
            for repo in chunk:
                repos.append(
                    {
                        "id": repo.get("id"),
                        "name": repo.get("name"),
                        "full_name": repo.get("full_name"),
                        "owner": (repo.get("owner") or {}).get("login"),
                        "private": repo.get("private"),
                        "default_branch": repo.get("default_branch"),
                        "language": repo.get("language"),
                        "html_url": repo.get("html_url"),
                        "updated_at": repo.get("updated_at"),
                    }
                )
            if len(chunk) < _PER_PAGE:
                break
    return repos


async def list_user_orgs(access_token: str) -> list[dict[str, Any]]:
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        r = await client.get(
            f"{GITHUB_API_BASE}/user/orgs",
            headers=_headers(access_token),
            params={"per_page": _PER_PAGE},
        )
        r.raise_for_status()
        data = r.json() if isinstance(r.json(), list) else []
        return [
            {"login": o.get("login"), "id": o.get("id"), "description": o.get("description")}
            for o in data
        ]


async def fetch_token_scopes(access_token: str) -> list[str]:
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        r = await client.get(
            f"{GITHUB_API_BASE}/user", headers=_headers(access_token)
        )
        r.raise_for_status()
        raw = r.headers.get("x-oauth-scopes", "")
        return [s.strip() for s in raw.split(",") if s.strip()]


async def list_org_repos(access_token: str, org: str) -> list[dict[str, Any]]:
    repos: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        for page in range(1, _MAX_PAGES + 1):
            r = await client.get(
                f"{GITHUB_API_BASE}/orgs/{org}/repos",
                headers=_headers(access_token),
                params={"per_page": _PER_PAGE, "page": page, "type": "all"},
            )
            r.raise_for_status()
            chunk = r.json()
            if not isinstance(chunk, list) or not chunk:
                break
            for repo in chunk:
                repos.append(
                    {
                        "id": repo.get("id"),
                        "name": repo.get("name"),
                        "full_name": repo.get("full_name"),
                        "owner": (repo.get("owner") or {}).get("login"),
                        "private": repo.get("private"),
                        "default_branch": repo.get("default_branch"),
                        "language": repo.get("language"),
                        "html_url": repo.get("html_url"),
                        "updated_at": repo.get("updated_at"),
                    }
                )
            if len(chunk) < _PER_PAGE:
                break
    return repos


async def list_repo_branches(
    access_token: str, owner: str, repo: str
) -> list[dict[str, Any]]:
    branches: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        for page in range(1, _MAX_PAGES + 1):
            r = await client.get(
                f"{GITHUB_API_BASE}/repos/{owner}/{repo}/branches",
                headers=_headers(access_token),
                params={"per_page": _PER_PAGE, "page": page},
            )
            r.raise_for_status()
            chunk = r.json()
            if not isinstance(chunk, list) or not chunk:
                break
            for br in chunk:
                commit = br.get("commit") or {}
                branches.append(
                    {
                        "name": br.get("name"),
                        "protected": br.get("protected", False),
                        "commit_sha": commit.get("sha"),
                    }
                )
            if len(chunk) < _PER_PAGE:
                break
    return branches
