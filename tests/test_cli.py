from pathlib import Path

from haifa_agent_evals import cli
from haifa_agent_evals.config import Candidate, EvaluationConfig


def test_registry_run_does_not_pass_default_doctor_path_as_local_tasks(
    tmp_path: Path, monkeypatch
) -> None:
    config = EvaluationConfig(
        id="swebench-smoke",
        dataset=f"swe-bench/swe-bench-verified@sha256:{'a' * 64}",
        tasks=("swe-bench/psf__requests-1142",),
        attempts=1,
        timeout_minutes=20,
        candidates=(Candidate("haifa", "package:Haifa", "deepseek/model"),),
        dataset_trust="upstream-verified",
    )
    captured: dict[str, object] = {}

    monkeypatch.setattr(cli, "load_config", lambda _path: config)
    monkeypatch.setattr(
        cli, "configured_tasks_path", lambda _config, _path: tmp_path / "default-selected"
    )
    monkeypatch.setattr(cli, "doctor", lambda *_args, **_kwargs: {"status": "READY"})

    def fake_run(*args: object) -> Path:
        captured["tasks_path"] = args[3]
        return tmp_path / "plan.json"

    monkeypatch.setattr(cli, "run", fake_run)

    result = cli.main(
        [
            "run",
            "--config",
            "eval.yaml",
            "--work-dir",
            str(tmp_path / "run"),
        ]
    )

    assert result == 0
    assert captured["tasks_path"] is None


def test_plan_only_preserves_explicit_admission(tmp_path: Path, monkeypatch) -> None:
    config = EvaluationConfig(
        id="swebench-smoke",
        dataset=f"swe-bench/swe-bench-verified@sha256:{'a' * 64}",
        tasks=("swe-bench/psf__requests-1142",),
        attempts=1,
        timeout_minutes=20,
        candidates=(Candidate("haifa", "package:Haifa", "deepseek/model"),),
        dataset_trust="upstream-verified",
    )
    captured: dict[str, object] = {}
    admission = tmp_path / "admission.json"
    monkeypatch.setattr(cli, "load_config", lambda _path: config)

    def fake_run(*args: object) -> Path:
        captured["admission"] = args[5]
        return tmp_path / "plan.json"

    monkeypatch.setattr(cli, "run", fake_run)

    result = cli.main(
        [
            "run",
            "--config",
            "eval.yaml",
            "--plan-only",
            "--admission",
            str(admission),
        ]
    )

    assert result == 0
    assert captured["admission"] == admission
