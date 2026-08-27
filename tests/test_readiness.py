from pathlib import Path

from haifa_agent_evals.config import Candidate, EvaluationConfig
from haifa_agent_evals.readiness import (
    EXPECTED_JAVA_ARCHIVE_SHA256,
    _java_archive_check,
    _plan_check,
    _task_set_check,
)


def _config(task_count: int = 30) -> EvaluationConfig:
    return EvaluationConfig(
        id="swebench-30",
        dataset="swe-bench/swe-bench-verified@sha256:abc",
        tasks=tuple(f"swe-bench/task-{index}" for index in range(task_count)),
        attempts=1,
        timeout_minutes=20,
        candidates=(Candidate("haifa", "package:Haifa", "gpt-5.6-terra", "openai-codex"),),
        dataset_trust="upstream-verified",
        concurrency=2,
    )


def test_task_set_requires_the_expected_number_of_unique_tasks() -> None:
    assert _task_set_check(_config(), 30)["status"] == "PASS"
    assert _task_set_check(_config(29), 30)["status"] == "FAIL"


def test_java_archive_check_requires_the_pinned_digest(tmp_path: Path, monkeypatch) -> None:
    archive = tmp_path / "temurin.tar.gz"
    archive.write_bytes(b"jdk")
    monkeypatch.setattr(
        "haifa_agent_evals.readiness._sha256",
        lambda path: EXPECTED_JAVA_ARCHIVE_SHA256,
    )

    assert _java_archive_check(archive)["status"] == "PASS"
    archive.unlink()
    assert _java_archive_check(archive)["status"] == "FAIL"


def test_harbor_plan_checks_candidate_and_task_matrix(tmp_path: Path) -> None:
    result = _plan_check(_config(), tmp_path / "tasks")

    assert result == {
        "name": "harbor-plan",
        "status": "PASS",
        "detail": "1 candidate(s) x 30 task(s) can be planned",
    }
