from __future__ import annotations

import json
import os
import time
from collections.abc import Mapping
from pathlib import Path

CODEX_AUTH_REFERENCE = "model-auth://openai-codex/default"
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
    current = environment or os.environ
    configured = current.get("HAIFA_EVAL_CODEX_AUTH_PATH", "").strip()
    return (
        Path(configured).expanduser().resolve()
        if configured
        else (Path.home() / ".haifa-agent" / "auth.json").resolve()
    )


def minimal_codex_auth(path: Path) -> bytes:
    if not path.is_file():
        raise ValueError("Codex auth store is missing")
    if path.stat().st_size > _MAX_AUTH_FILE_BYTES:
        raise ValueError("Codex auth store exceeds the size limit")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("Codex auth store is unreadable") from error
    if not isinstance(raw, dict) or set(raw) != _ROOT_FIELDS or raw.get("version") != 1:
        raise ValueError("Codex auth store root is invalid")
    credentials = raw.get("credentials")
    if not isinstance(credentials, dict):
        raise ValueError("Codex auth credentials are invalid")
    credential = credentials.get(CODEX_AUTH_REFERENCE)
    if not isinstance(credential, dict) or set(credential) != _CREDENTIAL_FIELDS:
        raise ValueError("Codex auth credential is missing or invalid")
    if credential.get("kind") != "EXTERNAL" or credential.get("method_id") != "openai-codex":
        raise ValueError("Codex auth credential identity is invalid")
    if any(
        not isinstance(credential.get(field), str) or not credential[field].strip()
        for field in _TEXT_FIELDS
    ):
        raise ValueError("Codex auth credential text fields are invalid")
    if any(
        not isinstance(credential.get(field), int)
        or isinstance(credential[field], bool)
        or credential[field] <= 0
        for field in _INTEGER_FIELDS
    ):
        raise ValueError("Codex auth credential timestamps are invalid")
    minimum_expiry = int(time.time() * 1000) + _MINIMUM_TOKEN_LIFETIME_MILLIS
    if credential["expires_at_epoch_millis"] <= minimum_expiry:
        raise ValueError("Codex auth credential is expired or expires too soon")
    payload = {
        "version": 1,
        "credentials": {CODEX_AUTH_REFERENCE: credential},
    }
    return (json.dumps(payload, separators=(",", ":")) + "\n").encode()
