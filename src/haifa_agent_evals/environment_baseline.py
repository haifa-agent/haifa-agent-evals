from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import tomllib
from pathlib import Path
from typing import Any

from harbor.publisher.packager import Packager

from haifa_agent_evals.config import UPSTREAM_VERIFIED, EvaluationConfig, load_config
from haifa_agent_evals.image_cache import (
    _container_cli,
    _image_inventory,
    _inspect,
    _pinned_reference,
)

LOCK_NAME = "task-environment-lock.json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def baseline_lock_path(tasks_path: Path) -> Path:
    return tasks_path.expanduser().resolve().parent / LOCK_NAME


def _read_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is unreadable: {path}") from error
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object: {path}")
    return value


def _admitted_task_digests(config: EvaluationConfig, admission_path: Path) -> dict[str, str]:
    admission = _read_object(admission_path, "admission evidence")
    if (
        admission.get("status") != "ADMITTED"
        or admission.get("evalId") != config.id
        or admission.get("dataset") != config.dataset
        or admission.get("trustMode") != "UPSTREAM_VERIFIED"
    ):
        raise ValueError("admission evidence does not match the upstream-verified config")
    records = admission.get("tasks")
    if not isinstance(records, list):
        raise ValueError("admission evidence has no task records")
    digests = {
        record.get("task_id"): record.get("task_digest")
        for record in records
        if isinstance(record, dict) and record.get("status") == "ADMITTED"
    }
    if set(digests) != set(config.tasks) or any(
        not isinstance(digests[task], str) or not digests[task].startswith("sha256:")
        for task in config.tasks
    ):
        raise ValueError("admission evidence does not contain the exact configured task set")
    return {task: str(digests[task]) for task in config.tasks}


def _with_frozen_image(task_toml: str, image_reference: str) -> str:
    if re.search(r"(?m)^docker_image\s*=", task_toml):
        raise ValueError("source task already declares docker_image")
    marker = "[environment]"
    if marker not in task_toml:
        raise ValueError("task.toml has no [environment] section")
    return task_toml.replace(marker, f'{marker}\ndocker_image = "{image_reference}"', 1)


def _image_record(image_reference: str, inspected: dict[str, Any]) -> dict[str, Any]:
    record = {
        "reference": image_reference,
        "id": inspected.get("Id"),
        "digest": inspected.get("Digest"),
        "sizeBytes": inspected.get("Size"),
        "architecture": inspected.get("Architecture"),
        "os": inspected.get("Os"),
    }
    if (
        "@sha256:" not in image_reference
        or not isinstance(record["id"], str)
        or not record["id"]
        or not isinstance(record["digest"], str)
        or not record["digest"].startswith("sha256:")
        or not isinstance(record["sizeBytes"], int)
        or record["sizeBytes"] < 1
        or not isinstance(record["architecture"], str)
        or not isinstance(record["os"], str)
    ):
        raise ValueError("container image does not expose a complete immutable identity")
    return record


def _discover_source_image(task_slug: str, inventory: list[dict[str, Any]]) -> str:
    candidates: dict[str, dict[str, Any]] = {}
    for image in inventory:
        config = image.get("Config") if isinstance(image.get("Config"), dict) else {}
        repo_tags = image.get("RepoTags") if isinstance(image.get("RepoTags"), list) else []
        history = image.get("NamesHistory") if isinstance(image.get("NamesHistory"), list) else []
        names = [*repo_tags, *history]
        if config.get("WorkingDir") != "/testbed" or not any(
            task_slug in str(name) for name in names
        ):
            continue
        image_id = image.get("Id")
        if isinstance(image_id, str):
            candidates[image_id] = image
    if len(candidates) != 1:
        raise ValueError(
            f"expected one cached /testbed image for {task_slug}, found {len(candidates)}; "
            "pass an explicit --source-image task=image mapping"
        )
    return next(iter(candidates))


def _source_image_map(values: list[str] | None, tasks: tuple[str, ...]) -> dict[str, str]:
    if not values:
        return {}
    result: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            if len(tasks) != 1:
                raise ValueError("a bare --source-image is only valid for a one-task config")
            task, image = tasks[0], value
        else:
            task, image = value.split("=", 1)
        if task not in tasks or not image.strip() or task in result:
            raise ValueError(f"invalid --source-image mapping: {value}")
        result[task] = image.strip()
    return result


def validate_environment_baseline(
    config: EvaluationConfig,
    tasks_path: Path,
    admission_path: Path,
    *,
    container_cli: str | None = None,
) -> dict[str, Any]:
    resolved_tasks = tasks_path.expanduser().resolve()
    lock_path = baseline_lock_path(resolved_tasks)
    lock = _read_object(lock_path, "task environment lock")
    if (
        lock.get("schemaVersion") != 1
        or lock.get("evalId") != config.id
        or lock.get("dataset") != config.dataset
        or lock.get("tasks") != list(config.tasks)
    ):
        raise ValueError("task environment lock identity does not match evaluation config")
    if lock.get("sourceAdmissionSha256") != _sha256(admission_path):
        raise ValueError("task environment lock does not match admission evidence")
    admitted = _admitted_task_digests(config, admission_path)
    if lock.get("sourceTaskDigests") != admitted:
        raise ValueError("task environment lock source digests do not match admission")

    derived = lock.get("frozenTaskDigests")
    images = lock.get("images")
    if not isinstance(derived, dict) or not isinstance(images, dict):
        raise ValueError("task environment lock is incomplete")
    cli = _container_cli(container_cli)
    for task in config.tasks:
        task_path = resolved_tasks / task.rsplit("/", 1)[-1]
        if not task_path.is_dir():
            raise ValueError(f"frozen task directory is missing: {task}")
        actual = f"sha256:{Packager.compute_content_hash(task_path)[0]}"
        if derived.get(task) != actual:
            raise ValueError(f"frozen task digest does not match lock: {task}")
        record = images.get(task)
        if not isinstance(record, dict) or not isinstance(record.get("reference"), str):
            raise ValueError(f"frozen image record is missing: {task}")
        task_toml = tomllib.loads((task_path / "task.toml").read_text(encoding="utf-8"))
        task_environment = task_toml.get("environment")
        if (
            not isinstance(task_environment, dict)
            or task_environment.get("docker_image") != record["reference"]
        ):
            raise ValueError(f"task docker_image does not match lock: {task}")
        inspected = _inspect(cli, record["reference"])
        actual_image = _image_record(record["reference"], inspected)
        if actual_image != record:
            raise ValueError(f"local image identity does not match lock: {task}")
    return lock


def freeze_swebench_task_environments(
    config_path: Path,
    source_tasks_path: Path,
    admission_path: Path,
    output: Path | None = None,
    *,
    container_cli: str | None = None,
    source_images: list[str] | None = None,
) -> dict[str, Any]:
    config = load_config(config_path)
    if config.dataset_trust != UPSTREAM_VERIFIED:
        raise ValueError("SWE-bench environment freezing requires upstream-verified trust")
    source_tasks = source_tasks_path.expanduser().resolve()
    admission = admission_path.expanduser().resolve()
    admitted = _admitted_task_digests(config, admission)
    destination = (
        (
            output
            or Path(__file__).resolve().parents[2]
            / "work"
            / "cache"
            / "images"
            / "task-environments"
            / f"{config.id}-baseline-v1"
        )
        .expanduser()
        .resolve()
    )
    destination_tasks = destination / "tasks"
    if destination.exists():
        lock = validate_environment_baseline(
            config, destination_tasks, admission, container_cli=container_cli
        )
        return {
            "tasksPath": str(destination_tasks),
            "lock": str(destination / LOCK_NAME),
            "images": lock["images"],
            "reused": True,
        }

    cli = _container_cli(container_cli)
    inventory = _image_inventory(cli)
    explicit_images = _source_image_map(source_images, config.tasks)
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f"{config.id}-baseline-v1-", dir=destination.parent))
    staging_tasks = staging / "tasks"
    staging_tasks.mkdir()
    frozen_digests: dict[str, str] = {}
    image_records: dict[str, dict[str, Any]] = {}
    try:
        for task in config.tasks:
            slug = task.rsplit("/", 1)[-1]
            source_task = source_tasks / slug
            if not source_task.is_dir():
                raise ValueError(f"source task directory is missing: {task}")
            source_digest = f"sha256:{Packager.compute_content_hash(source_task)[0]}"
            if source_digest != admitted[task]:
                raise ValueError(f"source task digest does not match admission: {task}")

            source_image = explicit_images.get(task) or _discover_source_image(slug, inventory)
            source_inspected = _inspect(cli, source_image)
            config_data = (
                source_inspected.get("Config")
                if isinstance(source_inspected.get("Config"), dict)
                else {}
            )
            if config_data.get("WorkingDir") != "/testbed":
                raise ValueError(
                    f"source image does not use the SWE-bench /testbed workspace: {task}"
                )
            safe_slug = re.sub(r"[^a-z0-9_.-]+", "-", slug.lower())
            stable_tag = (
                f"localhost/haifa-agent-evals/swebench-{safe_slug}:"
                f"task-{source_digest.removeprefix('sha256:')[:16]}"
            )
            subprocess.run([cli, "tag", source_image, stable_tag], check=True)  # noqa: S603
            stable_inspected = _inspect(cli, stable_tag)
            stable_reference = _pinned_reference(stable_tag, stable_inspected)

            frozen_task = staging_tasks / slug
            shutil.copytree(source_task, frozen_task)
            task_toml = frozen_task / "task.toml"
            task_toml.write_text(
                _with_frozen_image(task_toml.read_text(encoding="utf-8"), stable_reference),
                encoding="utf-8",
            )
            frozen_digests[task] = f"sha256:{Packager.compute_content_hash(frozen_task)[0]}"
            image_records[task] = _image_record(stable_reference, stable_inspected)

        lock: dict[str, Any] = {
            "schemaVersion": 1,
            "evalId": config.id,
            "dataset": config.dataset,
            "tasks": list(config.tasks),
            "sourceAdmissionSha256": _sha256(admission),
            "sourceTaskDigests": admitted,
            "frozenTaskDigests": frozen_digests,
            "images": image_records,
        }
        (staging / LOCK_NAME).write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")
        staging.rename(destination)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return {
        "tasksPath": str(destination_tasks),
        "lock": str(destination / LOCK_NAME),
        "images": image_records,
        "reused": False,
    }
