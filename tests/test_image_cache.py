import hashlib
import json
from pathlib import Path
from subprocess import CompletedProcess

import pytest

from haifa_agent_evals import image_cache


def test_rejects_unpinned_java_archive(tmp_path: Path) -> None:
    archive = tmp_path / "jdk.tar.gz"
    archive.write_bytes(b"wrong")

    with pytest.raises(ValueError, match="digest does not match"):
        image_cache._java_archive(archive)


def test_lock_payload_records_image_identity() -> None:
    payload = image_cache._lock_payload(
        "localhost/example:v1",
        {
            "Id": "sha256:image-id",
            "Digest": "sha256:manifest",
            "RepoDigests": ["localhost/example@sha256:manifest"],
            "Size": 123,
            "Architecture": "amd64",
            "Os": "linux",
            "Config": {"Labels": {"io.haifa.evals.contains-task-data": "false"}},
        },
    )

    assert payload["id"] == "sha256:image-id"
    assert payload["inputs"]["aiderVersion"] == "0.86.2"
    assert payload["labels"]["io.haifa.evals.contains-task-data"] == "false"
    assert json.loads(json.dumps(payload))["sizeBytes"] == 123


def test_pinned_reference_prefers_requested_repository() -> None:
    reference = image_cache._pinned_reference(
        "localhost/new:v1",
        {
            "RepoDigests": [
                "docker.io/old@sha256:exact",
                "localhost/new@sha256:exact",
            ]
        },
    )

    assert reference == "localhost/new@sha256:exact"


def test_rejects_incomplete_aider_runtime(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="runtime is incomplete"):
        image_cache._validate_aider_runtime(tmp_path / "missing.tar.gz")


def test_agent_ready_dockerfile_copies_only_infrastructure() -> None:
    generated = image_cache._agent_ready_dockerfile(
        "localhost/task@sha256:source",
        "localhost/infra@sha256:exact",
    )

    assert generated.startswith("FROM localhost/infra@sha256:exact AS haifa_agent_infra")
    assert "FROM localhost/task@sha256:source" in generated
    assert "/opt/haifa/java" in generated
    assert "haifa-agent.jar" not in generated


def test_agent_ready_dockerfile_can_replace_workspace_on_language_base() -> None:
    generated = image_cache._agent_ready_dockerfile(
        "localhost/task@sha256:source",
        "localhost/infra@sha256:exact",
        replace_workspace=True,
    )

    assert "find /app -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +" in generated
    assert "COPY workspace/ /app/" in generated


def test_task_prebuilt_image_is_digest_pinned() -> None:
    generated = image_cache._task_with_prebuilt_image(
        '[environment]\ncpus = 1\n',
        "localhost/task@sha256:exact",
    )

    assert '[environment]\ndocker_image = "localhost/task@sha256:exact"' in generated


def test_host_tree_hash_uses_case_sensitive_posix_relative_order(tmp_path: Path) -> None:
    (tmp_path / "CMakeLists.txt").write_bytes(b"cmake\n")
    (tmp_path / "binary.cpp").write_bytes(b"cpp\n")
    expected = hashlib.sha256()
    for relative, content in (
        (b"CMakeLists.txt", b"cmake\n"),
        (b"binary.cpp", b"cpp\n"),
    ):
        expected.update(len(relative).to_bytes(4, "big"))
        expected.update(relative)
        expected.update(content)

    assert image_cache._host_tree_hash(tmp_path) == expected.hexdigest()


def test_image_inventory_batches_inspection(monkeypatch: pytest.MonkeyPatch) -> None:
    ids = [f"sha256:{index:064x}" for index in range(101)]
    inspect_batch_sizes: list[int] = []

    def run(command: list[str], **_kwargs: object) -> CompletedProcess[str]:
        if command[1:] == ["images", "-aq", "--no-trunc"]:
            return CompletedProcess(command, 0, stdout="\n".join(ids), stderr="")
        inspected = command[3:]
        inspect_batch_sizes.append(len(inspected))
        return CompletedProcess(
            command,
            0,
            stdout=json.dumps([{"Id": image_id} for image_id in inspected]),
            stderr="",
        )

    monkeypatch.setattr(image_cache.subprocess, "run", run)

    inventory = image_cache._image_inventory("podman")

    assert inspect_batch_sizes == [50, 50, 1]
    assert [entry["Id"] for entry in inventory] == ids
