from typing import Any
from urllib.parse import urlencode

import httpx

from app.config import get_settings

settings = get_settings()

GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_USER_URL = "https://api.github.com/user"
GITHUB_USER_EMAILS_URL = "https://api.github.com/user/emails"


def build_authorize_url(state: str) -> str:
    params = {
        "client_id": settings.github_client_id,
        "redirect_uri": settings.github_redirect_uri,
        "scope": settings.github_oauth_scopes,
        "state": state,
        "allow_signup": "true",
    }
    return f"{GITHUB_AUTHORIZE_URL}?{urlencode(params)}"


async def exchange_code_for_token(code: str) -> str:
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            GITHUB_TOKEN_URL,
            headers={"Accept": "application/json"},
            data={
                "client_id": settings.github_client_id,
                "client_secret": settings.github_client_secret,
                "code": code,
                "redirect_uri": settings.github_redirect_uri,
            },
        )
        resp.raise_for_status()
        data = resp.json()

    token = data.get("access_token")
    if not token:
        raise ValueError(f"github token exchange failed: {data}")
    return token


async def fetch_github_user(access_token: str) -> dict[str, Any]:
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    async with httpx.AsyncClient(timeout=10.0, headers=headers) as client:
        user_resp = await client.get(GITHUB_USER_URL)
        user_resp.raise_for_status()
        user_data = user_resp.json()

        email = user_data.get("email")
        if not email:
            emails_resp = await client.get(GITHUB_USER_EMAILS_URL)
            if emails_resp.status_code == 200:
                emails = emails_resp.json()
                primary = next(
                    (e for e in emails if e.get("primary") and e.get("verified")),
                    None,
                )
                if primary:
                    email = primary.get("email")

    # Return raw GitHub /user payload enriched with canonical aliases the
    # rest of the app (JWT claims, DB upsert, /auth/me response) relies on.
    # Raw fields like bio, company, location, blog, public_repos, followers,
    # following, html_url, created_at, updated_at, twitter_username, etc.
    # are preserved for the frontend to consume.
    result = dict(user_data)
    result["github_id"] = user_data["id"]
    result["github_login"] = user_data["login"]
    result["display_name"] = user_data.get("name")
    result["avatar_url"] = user_data.get("avatar_url")
    result["email"] = email
    return result
