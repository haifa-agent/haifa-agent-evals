from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from harbor.publisher.packager import Packager

from haifa_agent_evals.config import EvaluationConfig, load_config
from haifa_agent_evals.environment_baseline import (
    LOCK_NAME,
    _image_record,
    _read_object,
    validate_environment_baseline,
)
from haifa_agent_evals.image_cache import _container_cli, _inspect, _pinned_reference

BUILDER_CONTRACT = "swebench-registry-cache-v1"
_REGISTRY_PREFIX = re.compile(
    r"^[a-z0-9-]+-docker\.pkg\.dev/[a-z][a-z0-9:-]{4,62}/[a-z0-9][a-z0-9._-]{0,127}$"
)


def _registry_prefix(value: str) -> str:
    prefix = value.strip().rstrip("/")
    if not _REGISTRY_PREFIX.fullmatch(prefix):
        raise ValueError("registry prefix must be LOCATION-docker.pkg.dev/PROJECT/REPOSITORY")
    return prefix


def _replace_image(task_toml: str, image: str) -> str:
    updated, count = re.subn(
        r'(?m)^docker_image\s*=\s*"[^"]+"\s*$',
        f'docker_image = "{image}"',
        task_toml,
    )
    if count != 1:
        raise ValueError("frozen task must declare exactly one docker_image")
    return updated


def _cache_key(task: str, source_task_digest: str, image: dict[str, Any]) -> str:
    payload = {
        "builderContract": BUILDER_CONTRACT,
        "task": task,
        "sourceTaskDigest": source_task_digest,
        "sourceImageDigest": image["digest"],
        "architecture": image["architecture"],
        "os": image["os"],
    }
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _safe_image_name(task: str) -> str:
    slug = task.rsplit("/", 1)[-1].lower()
    return re.sub(r"[^a-z0-9._-]+", "-", slug).strip("-._")


def _registry_lock(config: EvaluationConfig, tasks_path: Path) -> dict[str, Any]:
    lock = _read_object(
        tasks_path.expanduser().resolve().parent / LOCK_NAME, "task environment lock"
    )
    if (
        lock.get("schemaVersion") != 2
        or lock.get("evalId") != config.id
        or lock.get("dataset") != config.dataset
        or lock.get("tasks") != list(config.tasks)
        or lock.get("builderContract") != BUILDER_CONTRACT
        or not isinstance(lock.get("registryPrefix"), str)
        or not isinstance(lock.get("cacheKeys"), dict)
        or set(lock["cacheKeys"]) != set(config.tasks)
    ):
        raise ValueError("registry task environment lock does not match evaluation config")
    return lock


def publish_swebench_cache(
    config_path: Path,
    tasks_path: Path,
    admission_path: Path,
    registry_prefix: str,
    output: Path,
    *,
    container_cli: str | None = None,
) -> dict[str, Any]:
    config = load_config(config_path)
    source_tasks = tasks_path.expanduser().resolve()
    admission = admission_path.expanduser().resolve()
    prefix = _registry_prefix(registry_prefix)
    destination = output.expanduser().resolve()
    source_lock = validate_environment_baseline(
        config, source_tasks, admission, container_cli=container_cli
    )
    if destination.exists():
        raise ValueError("registry baseline output already exists")

    cli = _container_cli(container_cli)
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f"{config.id}-registry-v1-", dir=destination.parent))
    staging_tasks = staging / "tasks"
    staging_tasks.mkdir()
    images: dict[str, dict[str, Any]] = {}
    cache_keys: dict[str, str] = {}
    frozen_digests: dict[str, str] = {}
    try:
        for task in config.tasks:
            source_record = source_lock["images"][task]
            cache_key = _cache_key(task, source_lock["sourceTaskDigests"][task], source_record)
            target = (
                f"{prefix}/{_safe_image_name(task)}:cache-{cache_key.removeprefix('sha256:')[:20]}"
            )
            subprocess.run([cli, "tag", source_record["reference"], target], check=True)  # noqa: S603
            subprocess.run([cli, "push", target], check=True)  # noqa: S603
            inspected = _inspect(cli, target)
            pinned = _pinned_reference(target, inspected)
            if not pinned.startswith(f"{prefix}/") or "@sha256:" not in pinned:
                raise ValueError(f"registry push did not return a pinned digest: {task}")

            source_task = source_tasks / task.rsplit("/", 1)[-1]
            frozen_task = staging_tasks / source_task.name
            shutil.copytree(source_task, frozen_task)
            task_toml = frozen_task / "task.toml"
            task_toml.write_text(
                _replace_image(task_toml.read_text(encoding="utf-8"), pinned),
                encoding="utf-8",
            )
            images[task] = _image_record(pinned, inspected)
            cache_keys[task] = cache_key
            frozen_digests[task] = f"sha256:{Packager.compute_content_hash(frozen_task)[0]}"

        lock = {
            "schemaVersion": 2,
            "evalId": config.id,
            "dataset": config.dataset,
            "tasks": list(config.tasks),
            "sourceAdmissionSha256": source_lock["sourceAdmissionSha256"],
            "sourceTaskDigests": source_lock["sourceTaskDigests"],
            "frozenTaskDigests": frozen_digests,
            "images": images,
            "registryPrefix": prefix,
            "builderContract": BUILDER_CONTRACT,
            "cacheKeys": cache_keys,
        }
        (staging / LOCK_NAME).write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")
        staging.rename(destination)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return {
        "status": "PUBLISHED",
        "tasksPath": str(destination / "tasks"),
        "lock": str(destination / LOCK_NAME),
        "imageCount": len(images),
        "images": images,
    }


def check_swebench_cache(
    config_path: Path,
    tasks_path: Path,
    *,
    container_cli: str | None = None,
) -> dict[str, Any]:
    config = load_config(config_path)
    lock = _registry_lock(config, tasks_path)
    cli = _container_cli(container_cli)
    for task in config.tasks:
        record = lock.get("images", {}).get(task)
        reference = record.get("reference") if isinstance(record, dict) else None
        if not isinstance(reference, str) or "@sha256:" not in reference:
            raise ValueError(f"registry image reference is not digest-pinned: {task}")
        completed = subprocess.run(  # noqa: S603
            [cli, "manifest", "inspect", reference],
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            raise ValueError(f"registry image is unavailable: {task}")
    return {"status": "READY", "imageCount": len(config.tasks)}


def restore_swebench_cache(
    config_path: Path,
    tasks_path: Path,
    admission_path: Path,
    *,
    container_cli: str | None = None,
) -> dict[str, Any]:
    config: EvaluationConfig = load_config(config_path)
    resolved_tasks = tasks_path.expanduser().resolve()
    lock = _registry_lock(config, resolved_tasks)
    cli = _container_cli(container_cli)
    pulled = 0
    for task in config.tasks:
        record = lock.get("images", {}).get(task)
        reference = record.get("reference") if isinstance(record, dict) else None
        if not isinstance(reference, str) or "@sha256:" not in reference:
            raise ValueError(f"registry image reference is not digest-pinned: {task}")
        try:
            _inspect(cli, reference)
        except subprocess.CalledProcessError:
            subprocess.run([cli, "pull", reference], check=True)  # noqa: S603
            pulled += 1
        actual = _image_record(reference, _inspect(cli, reference))
        if actual != record:
            raise ValueError(f"restored registry image identity does not match lock: {task}")
    validate_environment_baseline(
        config,
        resolved_tasks,
        admission_path.expanduser().resolve(),
        container_cli=cli,
    )
    return {"status": "RESTORED", "imageCount": len(config.tasks), "pulled": pulled}
