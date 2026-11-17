from __future__ import annotations

import json
import os
import time
from collections.abc import Mapping
from pathlib import Path

CODEX_AUTH_REFERENCE = "model-auth://openai-codex/default"
OPENCODE_GO_AUTH_REFERENCE = "model-auth://opencode-go/default"
_MAX_AUTH_FILE_BYTES = 1024 * 1024
_ROOT_FIELDS = {"version", "credentials"}
_CREDENTIAL_FIELDS = {
    "kind",
    "method_id",
    "client_registration_ref",
    "access_token",
    "refresh_token",
    "expires_at_epoch_millis",
    "issued_at_epoch_millis",
    "account_id",
}
_TEXT_FIELDS = {
    "client_registration_ref",
    "access_token",
    "refresh_token",
    "account_id",
}
_INTEGER_FIELDS = {"expires_at_epoch_millis", "issued_at_epoch_millis"}
_MINIMUM_TOKEN_LIFETIME_MILLIS = 5 * 60 * 1000


def codex_auth_path(environment: Mapping[str, str] | None = None) -> Path:
    return _auth_path("HAIFA_EVAL_CODEX_AUTH_PATH", environment)


def opencode_go_auth_path(environment: Mapping[str, str] | None = None) -> Path:
    return _auth_path("HAIFA_EVAL_OPENCODE_GO_AUTH_PATH", environment)


def _auth_path(variable: str, environment: Mapping[str, str] | None) -> Path:
    current = environment or os.environ
    configured = current.get(variable, "").strip()
    return (
        Path(configured).expanduser().resolve()
        if configured
        else (Path.home() / ".haifa-agent" / "auth.json").resolve()
    )


def minimal_codex_auth(path: Path) -> bytes:
    return _minimal_auth(path, CODEX_AUTH_REFERENCE, "openai-codex", "Codex")


def minimal_opencode_go_auth(path: Path) -> bytes:
    return _minimal_auth(
        path, OPENCODE_GO_AUTH_REFERENCE, "opencode-go", "OpenCode Go",
        registration="opencode-go-console-v1",
    )


def _minimal_auth(
    path: Path, reference: str, method: str, name: str, *, registration: str | None = None
) -> bytes:
    if not path.is_file():
        raise ValueError(f"{name} auth store is missing")
    if path.stat().st_size > _MAX_AUTH_FILE_BYTES:
        raise ValueError(f"{name} auth store exceeds the size limit")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{name} auth store is unreadable") from error
    if not isinstance(raw, dict) or set(raw) != _ROOT_FIELDS or raw.get("version") != 1:
        raise ValueError(f"{name} auth store root is invalid")
    credentials = raw.get("credentials")
    if not isinstance(credentials, dict):
        raise ValueError(f"{name} auth credentials are invalid")
    credential = credentials.get(reference)
    if not isinstance(credential, dict) or set(credential) != _CREDENTIAL_FIELDS:
        raise ValueError(f"{name} auth credential is missing or invalid")
    if (
        credential.get("kind") != "EXTERNAL"
        or credential.get("method_id") != method
        or (registration and credential.get("client_registration_ref") != registration)
    ):
        raise ValueError(f"{name} auth credential identity is invalid")
    if any(
        not isinstance(credential.get(field), str) or not credential[field].strip()
        for field in _TEXT_FIELDS
    ):
        raise ValueError(f"{name} auth credential text fields are invalid")
    if any(
        not isinstance(credential.get(field), int)
        or isinstance(credential[field], bool)
        or credential[field] <= 0
        for field in _INTEGER_FIELDS
    ):
        raise ValueError(f"{name} auth credential timestamps are invalid")
    minimum_expiry = int(time.time() * 1000) + _MINIMUM_TOKEN_LIFETIME_MILLIS
    if credential["expires_at_epoch_millis"] <= minimum_expiry:
        raise ValueError(f"{name} auth credential is expired or expires too soon")
    payload = {
        "version": 1,
        "credentials": {reference: credential},
    }
    return (json.dumps(payload, separators=(",", ":")) + "\n").encode()
