import runpy
import sys
from pathlib import Path

import pytest


def test_lock_verifier_accepts_normalized_package_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = Path(__file__).resolve().parents[1] / "infra" / "agent-base" / "verify_aider_lock.py"
    lock = tmp_path / "requirements.lock"
    lock.write_text("Example_Package==1.2.3\n", encoding="utf-8")

    class _Distribution:
        metadata = {"Name": "example.package"}
        version = "1.2.3"

    monkeypatch.setattr(
        "importlib.metadata.distributions",
        lambda: [_Distribution()],
    )
    monkeypatch.setattr(sys, "argv", [str(script), str(lock)])

    runpy.run_path(str(script))
