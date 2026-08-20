import json
from pathlib import Path

import pytest
import yaml
from harbor.models.dataset.manifest import DatasetInfo, DatasetManifest, DatasetTaskRef
from harbor.publisher.packager import Packager

from haifa_agent_evals.config import Candidate, EvaluationConfig, load_config
from haifa_agent_evals.runner import (
    _child_environment,
    _default_haifa_jar,
    _validate_local_dataset,
    build_commands,
    build_job_config,
    run,
)


def test_builds_one_harbor_job_for_all_candidates(tmp_path: Path) -> None:
    config = EvaluationConfig(
        id="smoke",
        dataset="org/data@v1",
        tasks=("task-a", "task-b"),
        attempts=1,
        timeout_minutes=20,
        candidates=(
            Candidate("haifa", "package:Haifa", "provider/model"),
            Candidate("aider", "aider", "provider/model"),
        ),
    )
    commands = build_commands(config, tmp_path)
    assert len(commands) == 1
    assert commands[0][:3] == ["harbor", "run", "--config"]

    job_config = build_job_config(config, tmp_path)
    assert job_config["n_concurrent_trials"] == 1
    assert len(job_config["agents"]) == 2
    assert job_config["datasets"][0]["ref"] == "v1"

    plan = run(config, tmp_path, plan_only=True)
    assert plan.is_file()
    assert (tmp_path.parent / "smoke-harbor-job.yaml").is_file()
    assert not any(path.name == "result.json" for path in tmp_path.rglob("result.json"))


def test_uses_complete_local_task_cache(tmp_path: Path, monkeypatch) -> None:
    tasks = tmp_path / "selected-tasks"
    (tasks / "task-a").mkdir(parents=True)
    config = EvaluationConfig(
        id="smoke",
        dataset="org/data@sha256:exact",
        tasks=("org/task-a",),
        attempts=1,
        timeout_minutes=20,
        candidates=(Candidate("aider", "aider", "provider/model"),),
    )
    monkeypatch.setenv("HAIFA_EVAL_TASKS_PATH", str(tasks))

    plan = run(config, tmp_path / "job", plan_only=True)

    plan_data = json.loads(plan.read_text(encoding="utf-8"))
    assert plan_data["datasetSource"] == str(tasks.resolve())
    job_config = (tmp_path / "smoke-harbor-job.yaml").read_text(encoding="utf-8")
    assert "path:" in job_config
    assert "name: org/data" not in job_config


def test_adds_one_explicit_docker_compose_overlay(tmp_path: Path, monkeypatch) -> None:
    overlay = tmp_path / "cache.compose.yaml"
    overlay.write_text("services: {}\n", encoding="utf-8")
    monkeypatch.setenv("HAIFA_EVAL_EXTRA_DOCKER_COMPOSE", str(overlay))
    config = EvaluationConfig(
        id="smoke",
        dataset="org/data@sha256:exact",
        tasks=("org/task-a",),
        attempts=1,
        timeout_minutes=20,
        candidates=(Candidate("aider", "aider", "provider/model"),),
    )

    plan = run(config, tmp_path / "job", plan_only=True)

    plan_data = json.loads(plan.read_text(encoding="utf-8"))
    assert plan_data["extraDockerCompose"] == str(overlay.resolve())
    job_config = yaml.safe_load(
        (tmp_path / "smoke-harbor-job.yaml").read_text(encoding="utf-8")
    )
    assert job_config["environment"]["extra_docker_compose"] == [str(overlay.resolve())]


def test_rejects_missing_docker_compose_overlay(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HAIFA_EVAL_EXTRA_DOCKER_COMPOSE", str(tmp_path / "missing.yaml"))
    config = EvaluationConfig(
        id="smoke",
        dataset="org/data@sha256:exact",
        tasks=("org/task-a",),
        attempts=1,
        timeout_minutes=20,
        candidates=(Candidate("aider", "aider", "provider/model"),),
    )

    with pytest.raises(ValueError, match="does not point to a file"):
        run(config, tmp_path / "job", plan_only=True)


def test_validates_local_tasks_against_pinned_manifest(tmp_path: Path) -> None:
    tasks = tmp_path / "tasks"
    task = tasks / "task-a"
    task.mkdir(parents=True)
    (task / "task.toml").write_text(
        'version = "1.0"\n[task]\nname = "org/task-a"\n', encoding="utf-8"
    )
    (task / "instruction.md").write_text("solve it\n", encoding="utf-8")
    task_digest = f"sha256:{Packager.compute_content_hash(task)[0]}"
    manifest = DatasetManifest(
        dataset=DatasetInfo(name="org/data"),
        tasks=[DatasetTaskRef(name="org/task-a", digest=task_digest)],
    )
    manifest_path = tmp_path / "dataset.toml"
    manifest_path.write_text(manifest.to_toml(), encoding="utf-8")
    config = EvaluationConfig(
        id="smoke",
        dataset=f"org/data@sha256:{manifest.compute_content_hash()}",
        tasks=("org/task-a",),
        attempts=1,
        timeout_minutes=20,
        candidates=(Candidate("aider", "aider", "provider/model"),),
    )

    _validate_local_dataset(config, tasks, manifest_path)

    (task / "instruction.md").write_text("changed\n", encoding="utf-8")
    with pytest.raises(ValueError, match="local task digest does not match manifest"):
        _validate_local_dataset(config, tasks, manifest_path)


def test_default_jar_is_in_sibling_haifa_agent_repository() -> None:
    path = _default_haifa_jar()
    assert path.parts[-5:] == (
        "haifa-agent",
        "haifa-agent-applications",
        "haifa-agent-cli",
        "target",
        "haifa-agent-cli-0.1.0-SNAPSHOT.jar",
    )


def test_child_environment_forces_utf8_for_harbor_output(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("PYTHONUTF8", "0")
    monkeypatch.setenv("PYTHONIOENCODING", "gbk")

    environment = _child_environment(tmp_path)

    assert environment["PYTHONUTF8"] == "1"
    assert environment["PYTHONIOENCODING"] == "utf-8"


def test_checked_in_aider_route_survives_harbor_provider_split(tmp_path: Path) -> None:
    repository = Path(__file__).resolve().parents[1]
    config = load_config(repository / "evals" / "coding-smoke-v1.yaml")

    job_config = build_job_config(config, tmp_path)

    aider = next(agent for agent in job_config["agents"] if agent.get("name") == "aider")
    assert aider["model_name"] == "openai/openai/deepseek-v4-flash"
