from __future__ import annotations

import json
import subprocess
from pathlib import Path

import yaml

from haifa_agent_evals import registry_cache


def _config(tmp_path: Path) -> Path:
    path = tmp_path / "eval.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "id": "swebench-cache",
                "dataset": f"swe-bench/swe-bench-verified@sha256:{'a' * 64}",
                "datasetTrust": "upstream-verified",
                "tasks": ["swe-bench/psf__requests-1142"],
                "attempts": 1,
                "timeoutMinutes": 20,
                "candidates": [
                    {"id": "haifa", "agent": "package:Haifa", "model": "deepseek/model"}
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return path


def _source_baseline(tmp_path: Path) -> tuple[Path, dict[str, object]]:
    root = tmp_path / "source-baseline"
    task = root / "tasks" / "psf__requests-1142"
    task.mkdir(parents=True)
    (task / "task.toml").write_text(
        'version = "1.0"\n[environment]\ndocker_image = "localhost/source@sha256:old"\n',
        encoding="utf-8",
    )
    record = {
        "reference": "localhost/source@sha256:old",
        "id": "sha256:image-id",
        "digest": "sha256:old",
        "sizeBytes": 123,
        "architecture": "amd64",
        "os": "linux",
    }
    lock: dict[str, object] = {
        "schemaVersion": 1,
        "sourceAdmissionSha256": "b" * 64,
        "sourceTaskDigests": {"swe-bench/psf__requests-1142": "sha256:source"},
        "images": {"swe-bench/psf__requests-1142": record},
    }
    return root / "tasks", lock


def _registry_inspect() -> dict[str, object]:
    reference = (
        "asia-east1-docker.pkg.dev/test-project/haifa-eval-images/"
        "psf__requests-1142@sha256:registry"
    )
    return {
        "Id": "sha256:image-id",
        "Digest": "sha256:registry",
        "RepoDigests": [reference],
        "Size": 123,
        "Architecture": "amd64",
        "Os": "linux",
    }


def test_publish_pushes_and_writes_digest_pinned_registry_lock(tmp_path, monkeypatch) -> None:
    config = _config(tmp_path)
    tasks, source_lock = _source_baseline(tmp_path)
    calls: list[list[str]] = []
    monkeypatch.setattr(
        registry_cache, "validate_environment_baseline", lambda *_a, **_k: source_lock
    )
    monkeypatch.setattr(registry_cache, "_container_cli", lambda _value: "docker")
    monkeypatch.setattr(registry_cache, "_inspect", lambda *_args: _registry_inspect())
    monkeypatch.setattr(
        registry_cache.subprocess,
        "run",
        lambda command, **_kwargs: calls.append(command) or subprocess.CompletedProcess(command, 0),
    )
    output = tmp_path / "registry-baseline"

    result = registry_cache.publish_swebench_cache(
        config,
        tasks,
        tmp_path / "admission.json",
        "asia-east1-docker.pkg.dev/test-project/haifa-eval-images",
        output,
        container_cli="docker",
    )

    lock = json.loads((output / "task-environment-lock.json").read_text(encoding="utf-8"))
    task_text = (output / "tasks" / "psf__requests-1142" / "task.toml").read_text(encoding="utf-8")
    assert result["status"] == "PUBLISHED"
    assert lock["schemaVersion"] == 2
    assert lock["builderContract"] == registry_cache.BUILDER_CONTRACT
    assert "@sha256:registry" in lock["images"]["swe-bench/psf__requests-1142"]["reference"]
    assert "@sha256:registry" in task_text
    assert [call[1] for call in calls] == ["tag", "push"]


def test_check_requires_every_remote_digest(tmp_path, monkeypatch) -> None:
    config = _config(tmp_path)
    tasks, _ = _source_baseline(tmp_path)
    lock = {
        "schemaVersion": 2,
        "evalId": "swebench-cache",
        "dataset": f"swe-bench/swe-bench-verified@sha256:{'a' * 64}",
        "tasks": ["swe-bench/psf__requests-1142"],
        "builderContract": registry_cache.BUILDER_CONTRACT,
        "registryPrefix": "asia-east1-docker.pkg.dev/test-project/repo",
        "cacheKeys": {"swe-bench/psf__requests-1142": "sha256:cache"},
        "images": {
            "swe-bench/psf__requests-1142": {
                "reference": "asia-east1-docker.pkg.dev/test-project/repo/task@sha256:abc"
            }
        },
    }
    (tasks.parent / "task-environment-lock.json").write_text(json.dumps(lock), encoding="utf-8")
    monkeypatch.setattr(registry_cache, "_container_cli", lambda _value: "docker")
    monkeypatch.setattr(
        registry_cache.subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(command, 0, "{}", ""),
    )

    result = registry_cache.check_swebench_cache(config, tasks, container_cli="docker")

    assert result == {"status": "READY", "imageCount": 1}


def test_restore_pulls_missing_image_then_runs_full_baseline_validation(
    tmp_path, monkeypatch
) -> None:
    config = _config(tmp_path)
    tasks, _ = _source_baseline(tmp_path)
    inspected = _registry_inspect()
    reference = inspected["RepoDigests"][0]
    record = {
        "reference": reference,
        "id": inspected["Id"],
        "digest": inspected["Digest"],
        "sizeBytes": inspected["Size"],
        "architecture": inspected["Architecture"],
        "os": inspected["Os"],
    }
    lock = {
        "schemaVersion": 2,
        "evalId": "swebench-cache",
        "dataset": f"swe-bench/swe-bench-verified@sha256:{'a' * 64}",
        "tasks": ["swe-bench/psf__requests-1142"],
        "builderContract": registry_cache.BUILDER_CONTRACT,
        "registryPrefix": "asia-east1-docker.pkg.dev/test-project/repo",
        "cacheKeys": {"swe-bench/psf__requests-1142": "sha256:cache"},
        "images": {"swe-bench/psf__requests-1142": record},
    }
    (tasks.parent / "task-environment-lock.json").write_text(json.dumps(lock), encoding="utf-8")
    inspect_calls = 0

    def inspect(_cli, _reference):
        nonlocal inspect_calls
        inspect_calls += 1
        if inspect_calls == 1:
            raise subprocess.CalledProcessError(1, ["docker", "image", "inspect"])
        return inspected

    pulls: list[list[str]] = []
    validations: list[object] = []
    monkeypatch.setattr(registry_cache, "_container_cli", lambda _value: "docker")
    monkeypatch.setattr(registry_cache, "_inspect", inspect)
    monkeypatch.setattr(
        registry_cache.subprocess,
        "run",
        lambda command, **_kwargs: pulls.append(command) or subprocess.CompletedProcess(command, 0),
    )
    monkeypatch.setattr(
        registry_cache,
        "validate_environment_baseline",
        lambda *_args, **_kwargs: validations.append(True),
    )

    result = registry_cache.restore_swebench_cache(
        config, tasks, tmp_path / "admission.json", container_cli="docker"
    )

    assert result["pulled"] == 1
    assert pulls[0][1] == "pull"
    assert validations == [True]
