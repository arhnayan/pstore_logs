"""Credential storage in the local SQLite database."""

from __future__ import annotations

_USERNAME_KEY = "credentials_username"
_PASSWORD_KEY = "credentials_password"


async def get_credentials() -> tuple[str, str] | None:
    from app.deps import db

    username = await db.get_setting(_USERNAME_KEY)
    password = await db.get_setting(_PASSWORD_KEY)
    if username and password:
        return username, password
    return None


async def set_credentials(username: str, password: str) -> None:
    from app.deps import db

    await db.set_setting(_USERNAME_KEY, username)
    await db.set_setting(_PASSWORD_KEY, password)


async def clear_credentials() -> None:
    from app.deps import db

    await db.delete_setting(_USERNAME_KEY)
    await db.delete_setting(_PASSWORD_KEY)


async def has_credentials() -> bool:
    return (await get_credentials()) is not None
