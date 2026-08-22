import json
import os
from hashlib import sha256
from pathlib import Path

import pytest
import yaml
from harbor.models.dataset.manifest import DatasetInfo, DatasetManifest, DatasetTaskRef
from harbor.publisher.packager import Packager

from haifa_agent_evals.config import Candidate, EvaluationConfig, load_config
from haifa_agent_evals.runner import (
    _child_environment,
    _default_haifa_jar,
    _tooling_directory,
    _validate_local_dataset,
    build_commands,
    build_job_config,
    run,
)


def test_repository_work_uses_shared_tooling_cache(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("haifa_agent_evals.runner.repository_root", lambda: tmp_path)

    work_dir = tmp_path / "work" / "runs" / "evaluations" / "smoke" / "run-1"

    assert _tooling_directory(work_dir) == tmp_path / "work" / "cache" / "tooling"
    assert _tooling_directory(tmp_path / "external" / "run-1") == (
        tmp_path / "external" / ".tooling"
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

    work_dir = tmp_path / "run-1"
    plan = run(config, work_dir, plan_only=True)
    assert plan.is_file()
    assert (tmp_path / "run-1-harbor-job.yaml").is_file()
    manifest_path = tmp_path / "run-1-run-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["runId"] == "run-1"
    assert manifest["runStatus"] == "PLANNED"
    assert len(manifest["plannedTrials"]) == 4
    assert str(tmp_path) not in manifest_path.read_text(encoding="utf-8")
    assert not any(path.name == "result.json" for path in tmp_path.rglob("result.json"))


def test_builds_bailian_haifa_agent_with_separate_config(tmp_path: Path) -> None:
    config = EvaluationConfig(
        id="bailian-smoke",
        dataset="org/data@v1",
        tasks=("task-a",),
        attempts=1,
        timeout_minutes=20,
        candidates=(
            Candidate(
                "haifa",
                "haifa_agent_evals.integrations.harbor.haifa_agent:HaifaCodingAgent",
                "qwen3.7-max",
                "aliyun-bailian",
            ),
        ),
    )

    agent = build_job_config(config, tmp_path)["agents"][0]

    assert agent["model_name"] == "qwen3.7-max"
    assert agent["env"] == {
        "DASHSCOPE_API_KEY": "${DASHSCOPE_API_KEY}",
        "HAIFA_BAILIAN_ENDPOINT": "${HAIFA_BAILIAN_ENDPOINT}",
        "HAIFA_BAILIAN_MODEL_ID": "qwen3.7-max",
    }
    assert str(agent["kwargs"]["config_path"]).endswith(
        "haifa-eval-bailian-responses.yaml"
    )


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
    assert plan_data["datasetSource"] == "local"
    job_config = (tmp_path / "job-harbor-job.yaml").read_text(encoding="utf-8")
    assert "path:" in job_config
    assert "task_names:" in job_config
    assert "- task-a" in job_config
    assert "name: org/data" not in job_config


def test_filters_a_shared_local_dataset_to_configured_tasks(tmp_path: Path) -> None:
    config = EvaluationConfig(
        id="smoke",
        dataset="org/data@sha256:exact",
        tasks=("org/task-a", "org/task-c"),
        attempts=1,
        timeout_minutes=20,
        candidates=(Candidate("haifa", "package:Haifa", "provider/model"),),
    )

    job_config = build_job_config(config, tmp_path, tmp_path / "shared-tasks")

    assert job_config["datasets"] == [
        {
            "path": str((tmp_path / "shared-tasks").resolve()),
            "task_names": ["task-a", "task-c"],
        }
    ]


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
    assert plan_data["extraDockerCompose"] is True
    job_config = yaml.safe_load((tmp_path / "job-harbor-job.yaml").read_text(encoding="utf-8"))
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


def test_rejects_missing_explicit_dataset_manifest(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HAIFA_EVAL_DATASET_MANIFEST_PATH", str(tmp_path / "missing.toml"))
    config = EvaluationConfig(
        id="smoke",
        dataset="org/data@sha256:exact",
        tasks=("org/task-a",),
        attempts=1,
        timeout_minutes=20,
        candidates=(Candidate("aider", "aider", "provider/model"),),
    )

    with pytest.raises(ValueError, match="DATASET_MANIFEST_PATH"):
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


def test_child_environment_forces_utf8_for_harbor_output(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PYTHONUTF8", "0")
    monkeypatch.setenv("PYTHONIOENCODING", "gbk")

    environment = _child_environment(tmp_path)

    assert environment["PYTHONUTF8"] == "1"
    assert environment["PYTHONIOENCODING"] == "utf-8"


def test_child_environment_forces_explicit_podman_even_when_docker_exists(
    tmp_path: Path, monkeypatch
) -> None:
    podman = tmp_path / "podman.exe"
    docker = tmp_path / "real-docker.exe"
    podman.write_bytes(b"podman")
    docker.write_bytes(b"docker")

    def which(command: str | None, **_kwargs: object) -> str | None:
        if command == "podman":
            return str(podman)
        if command == "docker":
            return str(docker)
        return None

    monkeypatch.setattr("haifa_agent_evals.runner.shutil.which", which)

    environment = _child_environment(tmp_path / "job", "podman")

    wrapper = tmp_path / ".tooling" / "docker.exe"
    assert wrapper.read_bytes() == b"podman"
    assert environment["PATH"].split(os.pathsep, 1)[0] == str(wrapper.parent)


def test_checked_in_aider_route_survives_harbor_provider_split(tmp_path: Path) -> None:
    repository = Path(__file__).resolve().parents[1]
    config = load_config(repository / "evals" / "coding-smoke-v1.yaml")

    job_config = build_job_config(config, tmp_path)

    aider = next(
        agent
        for agent in job_config["agents"]
        if agent.get("import_path", "").endswith(":PinnedAiderAgent")
    )
    assert aider["model_name"] == "openai/openai/deepseek-v4-flash"
    assert aider["env"]["AIDER_DISABLE_PLAYWRIGHT"] == "true"


def test_run_refuses_to_reuse_a_run_directory(tmp_path: Path) -> None:
    config = EvaluationConfig(
        id="smoke",
        dataset="org/data@v1",
        tasks=("task-a",),
        attempts=1,
        timeout_minutes=20,
        candidates=(Candidate("aider", "aider", "provider/model"),),
    )
    work_dir = tmp_path / "existing-run"
    work_dir.mkdir()

    with pytest.raises(ValueError, match="run directory already exists"):
        run(config, work_dir, plan_only=True)


def test_run_manifest_fingerprints_preflight_evidence_and_explicit_jar(tmp_path: Path) -> None:
    config = EvaluationConfig(
        id="smoke",
        dataset="org/data@v1",
        tasks=("task-a",),
        attempts=1,
        timeout_minutes=20,
        candidates=(Candidate("haifa", "package:Haifa", "provider/model"),),
    )
    jar = tmp_path / "agent.jar"
    admission = tmp_path / "admission.json"
    preflight = tmp_path / "preflight.json"
    jar.write_bytes(b"jar")
    admission.write_bytes(b"admission")
    preflight.write_bytes(b"preflight")

    run(
        config,
        tmp_path / "run-1",
        plan_only=True,
        jar_path=jar,
        admission_path=admission,
        preflight_path=preflight,
    )

    manifest = json.loads((tmp_path / "run-1-run-manifest.json").read_text(encoding="utf-8"))
    assert manifest["haifaJarSha256"] == sha256(b"jar").hexdigest()
    assert manifest["admissionSha256"] == sha256(b"admission").hexdigest()
    assert manifest["preflightSha256"] == sha256(b"preflight").hexdigest()


def test_failed_run_updates_manifest_terminal_status(tmp_path: Path) -> None:
    config = EvaluationConfig(
        id="smoke",
        dataset="org/data@v1",
        tasks=("task-a",),
        attempts=1,
        timeout_minutes=20,
        candidates=(Candidate("haifa", "package:Haifa", "provider/model"),),
    )

    with pytest.raises(ValueError, match="readable JAR"):
        run(config, tmp_path / "run-1", jar_path=tmp_path / "missing.jar")

    manifest = json.loads((tmp_path / "run-1-run-manifest.json").read_text(encoding="utf-8"))
    assert manifest["runStatus"] == "HARBOR_FAILED"
    assert manifest["finishedAt"]
