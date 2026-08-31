"""Credential storage via macOS Keychain."""

from __future__ import annotations

import keyring

from app.config import settings


def get_credentials() -> tuple[str, str] | None:
    username = keyring.get_password(settings.keyring_service, "username")
    password = keyring.get_password(settings.keyring_service, "password")
    if username and password:
        return username, password
    return None


def set_credentials(username: str, password: str) -> None:
    keyring.set_password(settings.keyring_service, "username", username)
    keyring.set_password(settings.keyring_service, "password", password)


def clear_credentials() -> None:
    try:
        keyring.delete_password(settings.keyring_service, "username")
    except keyring.errors.PasswordDeleteError:
        pass
    try:
        keyring.delete_password(settings.keyring_service, "password")
    except keyring.errors.PasswordDeleteError:
        pass


def has_credentials() -> bool:
    return get_credentials() is not None
