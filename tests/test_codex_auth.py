import json
from pathlib import Path

import pytest

from haifa_agent_evals.codex_auth import CODEX_AUTH_REFERENCE, minimal_codex_auth


def _credential() -> dict[str, object]:
    return {
        "kind": "EXTERNAL",
        "method_id": "openai-codex",
        "client_registration_ref": "local-compat",
        "access_token": "access-secret",
        "refresh_token": "refresh-secret",
        "expires_at_epoch_millis": 2_000_000_000_000,
        "issued_at_epoch_millis": 1_900_000_000_000,
        "account_id": "account-secret",
    }


def test_minimal_codex_auth_drops_every_unselected_credential(tmp_path: Path) -> None:
    source = tmp_path / "auth.json"
    source.write_text(
        json.dumps(
            {
                "version": 1,
                "credentials": {
                    CODEX_AUTH_REFERENCE: _credential(),
                    "model-auth://other/default": {"kind": "API_KEY", "secret": "other"},
                },
            }
        ),
        encoding="utf-8",
    )

    projected = json.loads(minimal_codex_auth(source))

    assert set(projected["credentials"]) == {CODEX_AUTH_REFERENCE}
    assert "other" not in json.dumps(projected)


def test_minimal_codex_auth_rejects_unknown_fields(tmp_path: Path) -> None:
    source = tmp_path / "auth.json"
    credential = _credential()
    credential["unexpected"] = "secret"
    source.write_text(
        json.dumps({"version": 1, "credentials": {CODEX_AUTH_REFERENCE: credential}}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="missing or invalid"):
        minimal_codex_auth(source)


def test_minimal_codex_auth_rejects_expired_credential(tmp_path: Path) -> None:
    source = tmp_path / "auth.json"
    credential = _credential()
    credential["expires_at_epoch_millis"] = 1
    source.write_text(
        json.dumps({"version": 1, "credentials": {CODEX_AUTH_REFERENCE: credential}}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="expired or expires too soon"):
        minimal_codex_auth(source)
