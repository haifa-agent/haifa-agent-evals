from __future__ import annotations

import os
from pathlib import Path

from harbor.models.dataset.manifest import DatasetManifest
from harbor.publisher.packager import Packager

from haifa_agent_evals.config import EvaluationConfig


def repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


def dataset_manifest_path(config: EvaluationConfig) -> Path:
    configured = os.environ.get("HAIFA_EVAL_DATASET_MANIFEST_PATH")
    if configured:
        path = Path(configured).expanduser().resolve()
        if not path.is_file():
            raise ValueError("HAIFA_EVAL_DATASET_MANIFEST_PATH does not point to a file")
        return path
    return repository_root() / "evals" / f"{config.id}.dataset.toml"


def validate_local_dataset(
    config: EvaluationConfig,
    tasks_path: Path,
    manifest_path: Path,
) -> DatasetManifest:
    manifest = DatasetManifest.from_toml_file(manifest_path)
    dataset_name, dataset_ref = config.dataset.rsplit("@", 1)
    if manifest.dataset.name != dataset_name:
        raise ValueError("local dataset manifest name does not match evaluation config")
    actual_dataset_ref = f"sha256:{manifest.compute_content_hash()}"
    if actual_dataset_ref != dataset_ref:
        raise ValueError("local dataset manifest digest does not match evaluation config")

    manifest_tasks = {task.name: task.digest for task in manifest.tasks}
    if set(manifest_tasks) != set(config.tasks):
        raise ValueError("local dataset manifest tasks do not match evaluation config")
    for task_name in config.tasks:
        task_path = tasks_path / task_name.rsplit("/", 1)[-1]
        if not task_path.is_dir():
            raise ValueError(f"local task directory is missing: {task_name}")
        actual_task_digest = f"sha256:{Packager.compute_content_hash(task_path)[0]}"
        if actual_task_digest != manifest_tasks[task_name]:
            raise ValueError(f"local task digest does not match manifest: {task_name}")
    return manifest


def local_tasks_path(config: EvaluationConfig, work_dir: Path) -> Path | None:
    configured = os.environ.get("HAIFA_EVAL_TASKS_PATH")
    manifest_path = dataset_manifest_path(config)
    default_directory = "derived-tasks" if manifest_path.is_file() else "selected-tasks"
    candidate = Path(configured) if configured else work_dir.parent / default_directory
    expected_directories = {task.rsplit("/", 1)[-1] for task in config.tasks}
    if candidate.is_dir() and all((candidate / task).is_dir() for task in expected_directories):
        if manifest_path.is_file():
            validate_local_dataset(config, candidate, manifest_path)
        return candidate
    if configured:
        raise ValueError("HAIFA_EVAL_TASKS_PATH does not contain every configured task")
    if manifest_path.is_file():
        raise ValueError("pinned local dataset is missing; prepare work/derived-tasks first")
    return None
