import secrets
import tempfile

from html import escape
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.crypto import encrypt_token
from app.auth.github_oauth import (
    build_authorize_url,
    exchange_code_for_token,
    fetch_github_user,
)
from app.auth.jwt_utils import create_access_token, get_current_user
from app.auth.token_store import put_token
from app.config import get_settings
from app.db import get_db
from app.db_models import User

JWT_TEMP_FILE = Path(tempfile.gettempdir()) / "cicd_last_jwt.txt"

router = APIRouter(prefix="/auth", tags=["auth"])
settings = get_settings()

OAUTH_STATE_COOKIE = "gh_oauth_state"


@router.get("/github/login")
async def github_login() -> Response:
    state = secrets.token_urlsafe(32)
    url = build_authorize_url(state)
    resp = RedirectResponse(url=url, status_code=302)
    resp.set_cookie(
        key=OAUTH_STATE_COOKIE,
        value=state,
        max_age=600,
        httponly=True,
        samesite="lax",
        secure=False,
    )
    return resp


@router.get("/github/callback")
async def github_callback(
    code: str,
    state: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Response:
    cookie_state = request.cookies.get(OAUTH_STATE_COOKIE)
    if not cookie_state or not secrets.compare_digest(cookie_state, state):
        raise HTTPException(status_code=400, detail="invalid oauth state")

    access_token = await exchange_code_for_token(code)
    profile = await fetch_github_user(access_token)
    encrypted = encrypt_token(access_token)
    put_token(profile["github_id"], access_token)

    persisted_user_id: int | None = None
    try:
        result = await db.execute(
            select(User).where(User.github_id == profile["github_id"])
        )
        user = result.scalar_one_or_none()

        if user is None:
            user = User(
                github_id=profile["github_id"],
                github_login=profile["github_login"],
                display_name=profile["display_name"],
                avatar_url=profile["avatar_url"],
                email=profile["email"],
                github_access_token_encrypted=encrypted,
            )
            db.add(user)
        else:
            user.github_login = profile["github_login"]
            user.display_name = profile["display_name"]
            user.avatar_url = profile["avatar_url"]
            user.email = profile["email"]
            user.github_access_token_encrypted = encrypted

        await db.commit()
        await db.refresh(user)
        persisted_user_id = user.id
    except Exception as exc:
        await db.rollback()
        print(
            f"[auth] DB upsert skipped ({exc.__class__.__name__}: {exc}). "
            "Issuing JWT from GitHub profile only."
        )

    jwt_token = create_access_token(
        user_id=persisted_user_id,
        github_id=profile["github_id"],
        profile=profile,
    )

    try:
        JWT_TEMP_FILE.write_text(jwt_token, encoding="utf-8")
    except Exception as exc:
        print(f"[auth] failed to write JWT temp file: {exc}")

    redirect_target = f"{settings.frontend_redirect_url}?token={jwt_token}"
    resp = RedirectResponse(url=redirect_target, status_code=302)
    resp.delete_cookie(OAUTH_STATE_COOKIE)
    return resp


@router.get("/me")
async def get_me(current_user: dict = Depends(get_current_user)) -> dict:
    return current_user


@router.get("/success", response_class=HTMLResponse)
async def auth_success(token: str = "") -> HTMLResponse:
    safe_token = escape(token)
    html = f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><title>GitHub 로그인 성공</title>
<style>
body {{ font-family: -apple-system, Segoe UI, sans-serif; max-width: 760px;
       margin: 40px auto; padding: 0 20px; color: #222; }}
h1 {{ font-size: 22px; }}
code {{ background: #f4f4f4; padding: 2px 6px; border-radius: 4px; }}
textarea {{ width: 100%; height: 140px; font-family: ui-monospace, Consolas, monospace;
           font-size: 12px; padding: 10px; border: 1px solid #ccc; border-radius: 6px; }}
button {{ padding: 8px 14px; font-size: 14px; cursor: pointer; }}
.ok {{ color: #0a7d2e; font-weight: 600; }}
</style></head><body>
<h1>✅ <span class="ok">GitHub 로그인 성공</span></h1>
<p>아래 JWT를 복사하고, <a href="/docs" target="_blank">/docs</a>의 우측 상단
<strong>Authorize</strong> 버튼을 눌러 <code>Bearer &lt;토큰&gt;</code> 형태로 넣은 뒤
<code>/auth/me</code>를 호출해보세요.</p>
<textarea id="tk" readonly onclick="this.select()">{safe_token}</textarea>
<p>
  <button onclick="navigator.clipboard.writeText(document.getElementById('tk').value)">
    토큰 복사
  </button>
  &nbsp;
  <button onclick="fetch('/auth/me',{{headers:{{Authorization:'Bearer '+document.getElementById('tk').value}}}}).then(r=>r.json()).then(j=>document.getElementById('me').textContent=JSON.stringify(j,null,2))">
    /auth/me 호출
  </button>
</p>
<pre id="me" style="background:#f4f4f4;padding:12px;border-radius:6px;"></pre>
</body></html>"""
    return HTMLResponse(content=html)
