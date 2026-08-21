import json
from pathlib import Path

import pytest
from harbor.models.dataset.manifest import DatasetInfo, DatasetManifest, DatasetTaskRef
from harbor.publisher.packager import Packager

from haifa_agent_evals.admission import admit
from haifa_agent_evals.config import Candidate, EvaluationConfig


def _task(tasks_path: Path, name: str) -> Path:
    path = tasks_path / name.rsplit("/", 1)[-1]
    for directory in ("environment", "tests", "solution"):
        (path / directory).mkdir(parents=True, exist_ok=True)
    (path / "instruction.md").write_text("Implement the public contract.\n", encoding="utf-8")
    (path / "task.toml").write_text(
        f'version = "1.0"\n[task]\nname = "{name}"\n', encoding="utf-8"
    )
    return path


def _config_and_manifest(tmp_path: Path, monkeypatch) -> tuple[EvaluationConfig, Path]:
    tasks_path = tmp_path / "tasks"
    task_names = ("org/task-a", "org/task-b")
    refs = []
    for name in task_names:
        path = _task(tasks_path, name)
        refs.append(
            DatasetTaskRef(
                name=name,
                digest=f"sha256:{Packager.compute_content_hash(path)[0]}",
            )
        )
    manifest = DatasetManifest(dataset=DatasetInfo(name="org/data"), tasks=refs)
    manifest_path = tmp_path / "dataset.toml"
    manifest_path.write_text(manifest.to_toml(), encoding="utf-8")
    monkeypatch.setenv("HAIFA_EVAL_DATASET_MANIFEST_PATH", str(manifest_path))
    config = EvaluationConfig(
        id="smoke",
        dataset=f"org/data@sha256:{manifest.compute_content_hash()}",
        tasks=task_names,
        attempts=1,
        timeout_minutes=20,
        candidates=(Candidate("haifa", "package:Haifa", "provider/model"),),
    )
    return config, tasks_path


def _calibration(job_dir: Path, agent: str, rewards: dict[str, float | None]) -> None:
    for index, (task, reward) in enumerate(rewards.items(), start=1):
        trial = job_dir / f"trial-{index}"
        trial.mkdir(parents=True)
        (trial / "config.json").write_text(
            json.dumps({"task": {"name": task}, "agent": {"name": agent}}),
            encoding="utf-8",
        )
        (trial / "result.json").write_text(
            json.dumps(
                {
                    "task_name": task,
                    "agent_info": {"name": agent},
                    "verifier_result": (
                        None if reward is None else {"rewards": {"reward": reward}}
                    ),
                    "verifier": {"started_at": "2026-08-21T00:00:00Z"},
                    "exception_info": (
                        None
                        if reward is not None
                        else {"exception_type": "RewardFileNotFoundError"}
                    ),
                }
            ),
            encoding="utf-8",
        )


def test_admits_pinned_tasks_with_oracle_and_nop_evidence(tmp_path: Path, monkeypatch) -> None:
    config, tasks_path = _config_and_manifest(tmp_path, monkeypatch)
    oracle = tmp_path / "oracle"
    nop = tmp_path / "nop"
    _calibration(oracle, "oracle", {task: 1.0 for task in config.tasks})
    _calibration(nop, "nop", {task: 0.0 for task in config.tasks})
    output = tmp_path / "admission.json"

    result = admit(config, tasks_path, oracle, nop, output)

    assert result["status"] == "ADMITTED"
    assert [task["status"] for task in result["tasks"]] == ["ADMITTED", "ADMITTED"]
    assert result["tasks"][0]["verifier_selected"] is None
    assert "test counts are unavailable" in result["manualReviewFocus"][1]
    assert output.read_text(encoding="utf-8").endswith("\n")


def test_rejects_missing_or_untrusted_calibration(tmp_path: Path, monkeypatch) -> None:
    config, tasks_path = _config_and_manifest(tmp_path, monkeypatch)
    oracle = tmp_path / "oracle"
    nop = tmp_path / "nop"
    _calibration(oracle, "oracle", {config.tasks[0]: 1.0})
    _calibration(nop, "nop", {config.tasks[0]: None, config.tasks[1]: 0.0})
    output = tmp_path / "admission.json"

    with pytest.raises(ValueError, match="dataset admission rejected tasks"):
        admit(config, tasks_path, oracle, nop, output)

    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["status"] == "REJECTED"
    by_task = {task["task_id"]: task for task in report["tasks"]}
    assert "nop verifier did not produce a trusted reward" in by_task[config.tasks[0]]["reasons"]
    assert "missing oracle calibration" in by_task[config.tasks[1]]["reasons"]


def test_rejects_calibration_for_unknown_task(tmp_path: Path, monkeypatch) -> None:
    config, tasks_path = _config_and_manifest(tmp_path, monkeypatch)
    oracle = tmp_path / "oracle"
    nop = tmp_path / "nop"
    _calibration(oracle, "oracle", {**dict.fromkeys(config.tasks, 1.0), "org/other": 1.0})
    _calibration(nop, "nop", dict.fromkeys(config.tasks, 0.0))

    with pytest.raises(ValueError, match="unknown tasks"):
        admit(config, tasks_path, oracle, nop, tmp_path / "admission.json")
