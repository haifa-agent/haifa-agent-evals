from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import yaml

from haifa_agent_evals.config import Candidate, EvaluationConfig


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
        result["env"] = {"AIDER_OPENAI_API_BASE": "https://api.deepseek.com"}
    return result


def build_job_config(
    config: EvaluationConfig,
    work_dir: Path,
    tasks_path: Path | None = None,
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
        dataset = {"path": str(tasks_path.resolve())}
    return {
        "job_name": work_dir.name,
        "jobs_dir": str(work_dir.parent.resolve()),
        "n_attempts": config.attempts,
        "n_concurrent_trials": 1,
        "quiet": False,
        "retry": {"max_retries": 0},
        "environment": {"type": "docker", "delete": True},
        "verifier": {"override_timeout_sec": 600, "max_timeout_sec": 600},
        "agents": [
            _agent_config(candidate, config.timeout_minutes * 60) for candidate in config.candidates
        ],
        "datasets": [dataset],
    }


def build_commands(config: EvaluationConfig, work_dir: Path) -> list[list[str]]:
    job_config_path = work_dir.parent / f"{config.id}-harbor-job.yaml"
    return [["harbor", "run", "--config", str(job_config_path), "--yes"]]


def _child_environment(work_dir: Path) -> dict[str, str]:
    environment = os.environ.copy()
    if "DEEPSEEK_API_KEY" in environment:
        environment.setdefault("OPENAI_API_KEY", environment["DEEPSEEK_API_KEY"])
    podman = shutil.which("podman", path=environment.get("PATH"))
    if shutil.which("docker", path=environment.get("PATH")) is None and podman:
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


def _local_tasks_path(config: EvaluationConfig, work_dir: Path) -> Path | None:
    configured = os.environ.get("HAIFA_EVAL_TASKS_PATH")
    candidate = Path(configured) if configured else work_dir.parent / "selected-tasks"
    expected_directories = {task.rsplit("/", 1)[-1] for task in config.tasks}
    if candidate.is_dir() and all((candidate / task).is_dir() for task in expected_directories):
        return candidate
    if configured:
        raise ValueError("HAIFA_EVAL_TASKS_PATH does not contain every configured task")
    return None


def _write_inputs(config: EvaluationConfig, work_dir: Path) -> Path:
    work_dir.parent.mkdir(parents=True, exist_ok=True)
    tasks_path = _local_tasks_path(config, work_dir)
    job_config_path = work_dir.parent / f"{config.id}-harbor-job.yaml"
    job_config_path.write_text(
        yaml.safe_dump(build_job_config(config, work_dir, tasks_path), sort_keys=False),
        encoding="utf-8",
    )
    commands = build_commands(config, work_dir)
    plan_path = work_dir.parent / f"{config.id}-eval-plan.json"
    plan_path.write_text(
        json.dumps(
            {
                "eval_id": config.id,
                "dataset": config.dataset,
                "datasetSource": str(tasks_path.resolve()) if tasks_path else "registry",
                "tasks": list(config.tasks),
                "attempts": config.attempts,
                "commands": commands,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return plan_path


def run(config: EvaluationConfig, work_dir: Path, plan_only: bool = False) -> Path:
    plan_path = _write_inputs(config, work_dir)
    if not plan_only:
        environment = _child_environment(work_dir)
        environment.setdefault("HAIFA_EVAL_JAR_PATH", str(_default_haifa_jar()))
        if not Path(environment["HAIFA_EVAL_JAR_PATH"]).is_file():
            raise ValueError("HAIFA_EVAL_JAR_PATH does not point to a readable JAR")
        for command in build_commands(config, work_dir):
            subprocess.run(command, check=True, env=environment)  # noqa: S603
    return plan_path
