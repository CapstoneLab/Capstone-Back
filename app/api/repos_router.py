from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException

from app.api.github_client import (
    fetch_token_scopes,
    list_org_repos,
    list_repo_branches,
    list_user_orgs,
    list_user_repos,
)
from app.auth.jwt_utils import get_current_user
from app.auth.token_store import get_token

router = APIRouter(prefix="/api", tags=["github-api"])


def _resolve_token(current_user: dict[str, Any]) -> str:
    gid = current_user.get("github_id")
    if gid is None:
        raise HTTPException(status_code=401, detail="missing github_id in token")
    token = get_token(int(gid))
    if not token:
        raise HTTPException(
            status_code=401,
            detail="github access token not in cache; please log in again via /auth/github/login",
        )
    return token


@router.get("/repos")
async def get_repos(current_user: dict = Depends(get_current_user)) -> dict[str, Any]:
    token = _resolve_token(current_user)
    try:
        repos = await list_user_repos(token)
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=exc.response.status_code,
            detail=f"github api error: {exc.response.text}",
        ) from exc

    # Also fetch orgs directly — /user/repos sometimes omits org repos even with
    # affiliation=organization_member if the OAuth app hasn't been approved at org level.
    # Merging /orgs/{org}/repos catches the ones that slip through (and surfaces per-org errors).
    org_errors: list[dict[str, str]] = []
    try:
        orgs = await list_user_orgs(token)
    except httpx.HTTPStatusError as exc:
        orgs = []
        org_errors.append({"step": "list_user_orgs", "error": exc.response.text})

    seen: set[int] = {r["id"] for r in repos if r.get("id") is not None}
    for org in orgs:
        login = org.get("login")
        if not login:
            continue
        try:
            org_repos = await list_org_repos(token, login)
        except httpx.HTTPStatusError as exc:
            org_errors.append(
                {"org": login, "status": str(exc.response.status_code), "error": exc.response.text[:200]}
            )
            continue
        for r in org_repos:
            rid = r.get("id")
            if rid is None or rid in seen:
                continue
            seen.add(rid)
            repos.append(r)

    return {
        "count": len(repos),
        "orgs_detected": [o.get("login") for o in orgs],
        "org_errors": org_errors,
        "repos": repos,
    }


@router.get("/debug/whoami")
async def debug_whoami(current_user: dict = Depends(get_current_user)) -> dict[str, Any]:
    token = _resolve_token(current_user)
    result: dict[str, Any] = {"user": current_user}
    try:
        result["scopes"] = await fetch_token_scopes(token)
    except httpx.HTTPStatusError as exc:
        result["scopes_error"] = exc.response.text
    try:
        result["orgs"] = await list_user_orgs(token)
    except httpx.HTTPStatusError as exc:
        result["orgs_error"] = exc.response.text
    return result


@router.get("/repos/{owner}/{repo}/branches")
async def get_branches(
    owner: str, repo: str, current_user: dict = Depends(get_current_user)
) -> dict[str, Any]:
    token = _resolve_token(current_user)
    try:
        branches = await list_repo_branches(token, owner, repo)
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=exc.response.status_code,
            detail=f"github api error: {exc.response.text}",
        ) from exc
    return {
        "owner": owner,
        "repo": repo,
        "count": len(branches),
        "branches": branches,
    }
