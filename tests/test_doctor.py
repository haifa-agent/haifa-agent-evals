import json
from pathlib import Path

from harbor.models.dataset.manifest import DatasetInfo, DatasetManifest, DatasetTaskRef
from harbor.publisher.packager import Packager

from haifa_agent_evals.config import Candidate, EvaluationConfig
from haifa_agent_evals.doctor import MINIMUM_FREE_BYTES, doctor


def _fixture(tmp_path: Path, monkeypatch) -> tuple[EvaluationConfig, Path, Path]:
    tasks_path = tmp_path / "tasks"
    task_path = tasks_path / "task-a"
    task_path.mkdir(parents=True)
    (task_path / "task.toml").write_text(
        'version = "1.0"\n[task]\nname = "org/task-a"\n', encoding="utf-8"
    )
    (task_path / "instruction.md").write_text("solve\n", encoding="utf-8")
    manifest = DatasetManifest(
        dataset=DatasetInfo(name="org/data"),
        tasks=[
            DatasetTaskRef(
                name="org/task-a",
                digest=f"sha256:{Packager.compute_content_hash(task_path)[0]}",
            )
        ],
    )
    manifest_path = tmp_path / "dataset.toml"
    manifest_path.write_text(manifest.to_toml(), encoding="utf-8")
    monkeypatch.setenv("HAIFA_EVAL_DATASET_MANIFEST_PATH", str(manifest_path))
    config = EvaluationConfig(
        id="smoke",
        dataset=f"org/data@sha256:{manifest.compute_content_hash()}",
        tasks=("org/task-a",),
        attempts=1,
        timeout_minutes=20,
        candidates=(Candidate("haifa", "package:Haifa", "deepseek/model"),),
    )
    admission_path = tmp_path / "admission.json"
    admission_path.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "evalId": config.id,
                "dataset": config.dataset,
                "manifestDigest": config.dataset.rsplit("@", 1)[1],
                "status": "ADMITTED",
                "tasks": [{"task_id": "org/task-a", "status": "ADMITTED"}],
            }
        ),
        encoding="utf-8",
    )
    return config, tasks_path, admission_path


def test_doctor_reports_ready_without_exposing_credentials(tmp_path: Path, monkeypatch) -> None:
    config, tasks_path, admission_path = _fixture(tmp_path, monkeypatch)
    jar = tmp_path / "agent.jar"
    jar.write_bytes(b"fake jar")
    output = tmp_path / "preflight.json"

    result = doctor(
        config,
        tasks_path,
        admission_path,
        output,
        jar_path=jar,
        container_cli="podman",
        environment={"DEEPSEEK_API_KEY": "secret-value"},
        command_probe=lambda command: True,
        which=lambda command: command,
        free_bytes=MINIMUM_FREE_BYTES,
        harbor_version="0.20.0",
    )

    assert result["status"] == "READY"
    assert {check["status"] for check in result["checks"]} == {"PASS", "SKIP"}
    text = output.read_text(encoding="utf-8")
    assert "secret-value" not in text
    assert str(tmp_path) not in text
    assert result["admissionSha256"]


def test_doctor_blocks_missing_admission_credential_and_container(
    tmp_path: Path, monkeypatch
) -> None:
    config, tasks_path, _ = _fixture(tmp_path, monkeypatch)
    output = tmp_path / "preflight.json"

    result = doctor(
        config,
        tasks_path,
        tmp_path / "missing-admission.json",
        output,
        jar_path=tmp_path / "missing.jar",
        environment={},
        command_probe=lambda command: False,
        which=lambda command: None,
        free_bytes=0,
        harbor_version="0.19.0",
    )

    assert result["status"] == "BLOCKED"
    failed = {check["name"] for check in result["checks"] if check["status"] == "FAIL"}
    assert {"admission", "harbor", "container", "haifa-jar", "credentials", "disk"} <= failed
    assert "DEEPSEEK_API_KEY" in output.read_text(encoding="utf-8")


def test_doctor_blocks_admission_for_a_different_task_set(tmp_path: Path, monkeypatch) -> None:
    config, tasks_path, admission_path = _fixture(tmp_path, monkeypatch)
    admission = json.loads(admission_path.read_text(encoding="utf-8"))
    admission["tasks"] = [{"task_id": "org/other", "status": "ADMITTED"}]
    admission_path.write_text(json.dumps(admission), encoding="utf-8")
    jar = tmp_path / "agent.jar"
    jar.write_bytes(b"fake jar")

    result = doctor(
        config,
        tasks_path,
        admission_path,
        tmp_path / "preflight.json",
        jar_path=jar,
        container_cli="podman",
        environment={"DEEPSEEK_API_KEY": "present"},
        command_probe=lambda command: True,
        which=lambda command: command,
        free_bytes=MINIMUM_FREE_BYTES,
        harbor_version="0.20.0",
    )

    assert result["status"] == "BLOCKED"
    admission_check = next(check for check in result["checks"] if check["name"] == "admission")
    assert admission_check["detail"] == "admission task set does not match"


def test_doctor_blocks_proxy_run_without_harbor_compose_evidence(
    tmp_path: Path, monkeypatch
) -> None:
    config, tasks_path, admission_path = _fixture(tmp_path, monkeypatch)
    overlay = tmp_path / "proxy.compose.yaml"
    overlay.write_text("services: {}\n", encoding="utf-8")
    jar = tmp_path / "agent.jar"
    jar.write_bytes(b"fake jar")

    result = doctor(
        config,
        tasks_path,
        admission_path,
        tmp_path / "preflight.json",
        jar_path=jar,
        container_cli="podman",
        environment={
            "DEEPSEEK_API_KEY": "present",
            "HAIFA_EVAL_EXTRA_DOCKER_COMPOSE": str(overlay),
            "HAIFA_EVALS_CONTAINER_PROXY": "http://host.containers.internal:22081",
        },
        command_probe=lambda command: True,
        which=lambda command: command,
        free_bytes=MINIMUM_FREE_BYTES,
        harbor_version="0.20.0",
    )

    assert result["status"] == "BLOCKED"
    network = next(
        check for check in result["checks"] if check["name"] == "harbor-compose-network"
    )
    assert network["status"] == "FAIL"
    assert "evidence is required" in network["detail"]
