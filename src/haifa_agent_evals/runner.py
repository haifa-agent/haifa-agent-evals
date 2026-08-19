from __future__ import annotations

import json
import subprocess
from pathlib import Path

from haifa_agent_evals.config import EvaluationConfig


def build_commands(config: EvaluationConfig, work_dir: Path) -> list[list[str]]:
    commands: list[list[str]] = []
    for candidate in config.candidates:
        command = [
            "harbor",
            "run",
            "--dataset",
            config.dataset,
            "--agent",
            candidate.agent,
            "--model",
            candidate.model,
            "--n-attempts",
            str(config.attempts),
            "--n-concurrent",
            "1",
            "--jobs-dir",
            str(work_dir),
            "--job-name",
            f"{config.id}-{candidate.id}",
        ]
        for task in config.tasks:
            command.extend(["--include-task-name", task])
        commands.append(command)
    return commands


def run(config: EvaluationConfig, work_dir: Path, plan_only: bool = False) -> Path:
    work_dir.mkdir(parents=True, exist_ok=True)
    plan_path = work_dir / "eval-plan.json"
    commands = build_commands(config, work_dir)
    plan_path.write_text(
        json.dumps(
            {
                "eval_id": config.id,
                "dataset": config.dataset,
                "tasks": list(config.tasks),
                "attempts": config.attempts,
                "commands": commands,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    if not plan_only:
        for command in commands:
            subprocess.run(command, check=True)  # noqa: S603
    return plan_path
