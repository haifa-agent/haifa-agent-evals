import json
from pathlib import Path

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
