from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import uuid
from dataclasses import asdict
from datetime import UTC, datetime
from hashlib import sha256
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import yaml
from harbor.models.dataset.manifest import DatasetManifest

from haifa_agent_evals.config import Candidate, EvaluationConfig
from haifa_agent_evals.dataset import (
    dataset_manifest_path,
    local_tasks_path,
    validate_local_dataset,
)

_local_tasks_path = local_tasks_path
_validate_local_dataset = validate_local_dataset


def _agent_config(candidate: Candidate, timeout_seconds: int) -> dict[str, object]:
    result: dict[str, object] = {
        "model_name": candidate.model,
        "override_timeout_sec": timeout_seconds,
        "max_timeout_sec": timeout_seconds,
        "override_setup_timeout_sec": 600,
    }
    if ":" in candidate.agent:
        result["import_path"] = candidate.agent
    else:
        result["name"] = candidate.agent
    if candidate.id == "haifa":
        result["env"] = {"DEEPSEEK_API_KEY": "${DEEPSEEK_API_KEY}"}
    elif candidate.id == "aider":
        result["env"] = {
            "AIDER_OPENAI_API_BASE": "https://api.deepseek.com",
            "AIDER_DISABLE_PLAYWRIGHT": "true",
        }
    return result


def build_job_config(
    config: EvaluationConfig,
    work_dir: Path,
    tasks_path: Path | None = None,
    extra_docker_compose: Path | None = None,
) -> dict[str, object]:
    dataset_name, dataset_ref = config.dataset.rsplit("@", 1)
    dataset: dict[str, object]
    if tasks_path is None:
        dataset = {
            "name": dataset_name,
            "ref": dataset_ref,
            "task_names": list(config.tasks),
        }
    else:
        dataset = {
            "path": str(tasks_path.resolve()),
            "task_names": [task.rsplit("/", 1)[-1] for task in config.tasks],
        }
    environment: dict[str, object] = {"type": "docker", "delete": True}
    if extra_docker_compose is not None:
        environment["extra_docker_compose"] = [str(extra_docker_compose.resolve())]
    return {
        "job_name": work_dir.name,
        "jobs_dir": str(work_dir.parent.resolve()),
        "n_attempts": config.attempts,
        "n_concurrent_trials": 1,
        "quiet": False,
        "retry": {"max_retries": 0},
        "environment": environment,
        "verifier": {"override_timeout_sec": 600, "max_timeout_sec": 600},
        "agents": [
            _agent_config(candidate, config.timeout_minutes * 60) for candidate in config.candidates
        ],
        "datasets": [dataset],
    }


def build_commands(config: EvaluationConfig, work_dir: Path) -> list[list[str]]:
    job_config_path = work_dir.parent / f"{work_dir.name}-harbor-job.yaml"
    return [["harbor", "run", "--config", str(job_config_path), "--yes"]]


def new_run_id() -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{timestamp}-{uuid.uuid4().hex[:8]}"


def _file_sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _harbor_version() -> str:
    try:
        return version("harbor")
    except PackageNotFoundError:
        return "unavailable"


def _task_digests(config: EvaluationConfig) -> dict[str, str]:
    manifest_path = dataset_manifest_path(config)
    if not manifest_path.is_file():
        return {}
    manifest = DatasetManifest.from_toml_file(manifest_path)
    return {task.name: task.digest for task in manifest.tasks if task.name in config.tasks}


def config_sha256(config: EvaluationConfig) -> str:
    payload = json.dumps(asdict(config), sort_keys=True, separators=(",", ":"))
    return sha256(payload.encode()).hexdigest()


def _child_environment(work_dir: Path, container_cli: str | None = None) -> dict[str, str]:
    environment = os.environ.copy()
    # Harbor renders Unicode summary tables after a job. Force UTF-8 for the
    # child process so a successful Windows run cannot fail on a legacy code page.
    environment["PYTHONUTF8"] = "1"
    environment["PYTHONIOENCODING"] = "utf-8"
    if "DEEPSEEK_API_KEY" in environment:
        environment.setdefault("OPENAI_API_KEY", environment["DEEPSEEK_API_KEY"])
    configured = (
        shutil.which(container_cli, path=environment.get("PATH")) if container_cli else None
    )
    if container_cli and configured is None:
        raise ValueError(f"container CLI is unavailable: {container_cli}")
    podman = (
        configured
        if configured and Path(configured).stem.lower() == "podman"
        else shutil.which("podman", path=environment.get("PATH"))
    )
    force_podman = bool(configured and Path(configured).stem.lower() == "podman")
    if podman and (force_podman or shutil.which("docker", path=environment.get("PATH")) is None):
        tooling_dir = work_dir.parent / ".tooling"
        tooling_dir.mkdir(parents=True, exist_ok=True)
        wrapper = tooling_dir / "docker.exe"
        if not wrapper.is_file():
            shutil.copy2(podman, wrapper)
        environment["PATH"] = f"{tooling_dir}{os.pathsep}{environment.get('PATH', '')}"
    return environment


def _default_haifa_jar() -> Path:
    evaluation_repository = Path(__file__).resolve().parents[2]
    return (
        evaluation_repository.parent
        / "haifa-agent"
        / "haifa-agent-applications"
        / "haifa-agent-cli"
        / "target"
        / "haifa-agent-cli-0.1.0-SNAPSHOT.jar"
    )


def _extra_docker_compose() -> Path | None:
    configured = os.environ.get("HAIFA_EVAL_EXTRA_DOCKER_COMPOSE")
    if not configured:
        return None
    path = Path(configured)
    if not path.is_file():
        raise ValueError("HAIFA_EVAL_EXTRA_DOCKER_COMPOSE does not point to a file")
    return path


def _write_inputs(
    config: EvaluationConfig,
    work_dir: Path,
    tasks_path: Path | None = None,
    jar_path: Path | None = None,
    admission_path: Path | None = None,
    preflight_path: Path | None = None,
    infrastructure_evidence_path: Path | None = None,
) -> tuple[Path, Path]:
    if work_dir.exists():
        raise ValueError("run directory already exists; choose a new run id")
    work_dir.parent.mkdir(parents=True, exist_ok=True)
    tasks_path = _local_tasks_path(config, work_dir, tasks_path)
    extra_docker_compose = _extra_docker_compose()
    job_config_path = work_dir.parent / f"{work_dir.name}-harbor-job.yaml"
    plan_path = work_dir.parent / f"{work_dir.name}-eval-plan.json"
    manifest_path = work_dir.parent / f"{work_dir.name}-run-manifest.json"
    control_paths = (job_config_path, plan_path, manifest_path)
    if any(path.exists() for path in control_paths):
        raise ValueError("run control file already exists; choose a new run id")
    job_config_path.write_text(
        yaml.safe_dump(
            build_job_config(config, work_dir, tasks_path, extra_docker_compose),
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    commands = build_commands(config, work_dir)
    plan_path.write_text(
        json.dumps(
            {
                "eval_id": config.id,
                "dataset": config.dataset,
                "runId": work_dir.name,
                "datasetSource": "local" if tasks_path else "registry",
                "extraDockerCompose": extra_docker_compose is not None,
                "tasks": list(config.tasks),
                "attempts": config.attempts,
                "commands": [
                    [*command[:-2], job_config_path.name, command[-1]] for command in commands
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    resolved_jar = jar_path or Path(
        os.environ.get("HAIFA_EVAL_JAR_PATH", str(_default_haifa_jar()))
    )
    manifest = {
        "schemaVersion": 1,
        "runId": work_dir.name,
        "evalId": config.id,
        "configSha256": config_sha256(config),
        "dataset": config.dataset,
        "datasetSource": "local" if tasks_path else "registry",
        "tasks": list(config.tasks),
        "taskDigests": _task_digests(config),
        "candidates": [asdict(candidate) for candidate in config.candidates],
        "attempts": config.attempts,
        "timeoutMinutes": config.timeout_minutes,
        "plannedTrials": [
            {"candidate": candidate.id, "taskId": task, "attempt": attempt}
            for candidate in config.candidates
            for task in config.tasks
            for attempt in range(1, config.attempts + 1)
        ],
        "harborVersion": _harbor_version(),
        "pythonVersion": (
            f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        ),
        "haifaJarSha256": (
            _file_sha256(resolved_jar)
            if any(candidate.id == "haifa" for candidate in config.candidates)
            else None
        ),
        "admissionSha256": _file_sha256(admission_path) if admission_path else None,
        "preflightSha256": _file_sha256(preflight_path) if preflight_path else None,
        "infrastructureEvidenceSha256": (
            _file_sha256(infrastructure_evidence_path)
            if infrastructure_evidence_path
            else None
        ),
        "harborJobConfigSha256": _file_sha256(job_config_path),
        "extraDockerComposeSha256": (
            _file_sha256(extra_docker_compose) if extra_docker_compose else None
        ),
        "startedAt": datetime.now(UTC).isoformat(),
        "finishedAt": None,
        "runStatus": "PLANNED",
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return plan_path, manifest_path


def _finish_manifest(manifest_path: Path, status: str) -> None:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["finishedAt"] = datetime.now(UTC).isoformat()
    manifest["runStatus"] = status
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def run(
    config: EvaluationConfig,
    work_dir: Path,
    plan_only: bool = False,
    tasks_path: Path | None = None,
    jar_path: Path | None = None,
    admission_path: Path | None = None,
    preflight_path: Path | None = None,
    infrastructure_evidence_path: Path | None = None,
    container_cli: str | None = None,
) -> Path:
    plan_path, manifest_path = _write_inputs(
        config,
        work_dir,
        tasks_path,
        jar_path,
        admission_path,
        preflight_path,
        infrastructure_evidence_path,
    )
    if not plan_only:
        try:
            environment = _child_environment(work_dir, container_cli)
            if any(candidate.id == "haifa" for candidate in config.candidates):
                if jar_path is not None:
                    environment["HAIFA_EVAL_JAR_PATH"] = str(jar_path)
                else:
                    environment.setdefault("HAIFA_EVAL_JAR_PATH", str(_default_haifa_jar()))
                if not Path(environment["HAIFA_EVAL_JAR_PATH"]).is_file():
                    raise ValueError("HAIFA_EVAL_JAR_PATH does not point to a readable JAR")
            for command in build_commands(config, work_dir):
                subprocess.run(command, check=True, env=environment)  # noqa: S603
        except Exception:
            _finish_manifest(manifest_path, "HARBOR_FAILED")
            raise
        _finish_manifest(manifest_path, "HARBOR_FINISHED")
    return plan_path
