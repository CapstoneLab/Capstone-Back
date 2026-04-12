from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_db
from app.db_models import User

settings = get_settings()
bearer_scheme = HTTPBearer(auto_error=False)


def create_access_token(
    *,
    user_id: int | None,
    github_id: int,
    profile: dict[str, Any],
) -> str:
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": str(user_id) if user_id is not None else f"gh:{github_id}",
        "uid": user_id,
        "gid": github_id,
        "login": profile.get("github_login"),
        "name": profile.get("display_name"),
        "avatar": profile.get("avatar_url"),
        "email": profile.get("email"),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.jwt_expire_minutes)).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(
            token, settings.jwt_secret, algorithms=[settings.jwt_algorithm]
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail="invalid or expired token") from exc


async def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    if creds is None:
        raise HTTPException(status_code=401, detail="missing bearer token")
    payload = decode_token(creds.credentials)

    github_id = payload.get("gid")
    user_id = payload.get("uid")

    if github_id is None:
        raise HTTPException(status_code=401, detail="invalid token payload")

    try:
        result = await db.execute(select(User).where(User.github_id == int(github_id)))
        user = result.scalar_one_or_none()
        if user is not None:
            return {
                "id": user.id,
                "github_id": user.github_id,
                "github_login": user.github_login,
                "display_name": user.display_name,
                "avatar_url": user.avatar_url,
                "email": user.email,
                "source": "db",
            }
    except Exception:
        pass

    return {
        "id": user_id,
        "github_id": int(github_id),
        "github_login": payload.get("login"),
        "display_name": payload.get("name"),
        "avatar_url": payload.get("avatar"),
        "email": payload.get("email"),
        "source": "jwt",
    }
