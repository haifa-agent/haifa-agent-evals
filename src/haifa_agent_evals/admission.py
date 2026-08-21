from __future__ import annotations

import json
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from haifa_agent_evals.config import EvaluationConfig
from haifa_agent_evals.dataset import dataset_manifest_path, validate_local_dataset


@dataclass(frozen=True)
class CalibrationResult:
    task_id: str
    reward: float | None
    verifier_executed: bool
    error_type: str


@dataclass(frozen=True)
class TaskAdmission:
    task_id: str
    task_digest: str
    status: str
    oracle_reward: float | None
    nop_reward: float | None
    verifier_selected: int | None
    verifier_discovered: int | None
    verifier_ignored: int | None
    reasons: tuple[str, ...]


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _nested(value: dict[str, Any], *path: str) -> Any:
    current: Any = value
    for item in path:
        if not isinstance(current, dict):
            return None
        current = current.get(item)
    return current


def _reward(raw: dict[str, Any]) -> float | None:
    rewards = _nested(raw, "verifier_result", "rewards")
    if not isinstance(rewards, dict) or not rewards:
        return None
    value = rewards.get("reward")
    if value is None and len(rewards) == 1:
        value = next(iter(rewards.values()))
    return None if value is None else float(value)


def _calibration_results(job_dir: Path, expected_agent: str) -> dict[str, CalibrationResult]:
    if not job_dir.is_dir():
        raise ValueError(f"calibration job directory does not exist: {job_dir}")
    results: dict[str, CalibrationResult] = {}
    for path in sorted(job_dir.rglob("result.json")):
        if path.parent == job_dir or not (path.parent / "config.json").is_file():
            continue
        raw = _read_json(path)
        config = _read_json(path.parent / "config.json")
        agent = str(
            _nested(raw, "agent_info", "name")
            or _nested(config, "agent", "name")
            or ""
        )
        if agent != expected_agent:
            continue
        task_id = str(raw.get("task_name") or _nested(config, "task", "name") or "")
        if not task_id:
            raise ValueError(f"calibration trial has no task identity: {path.parent.name}")
        if task_id in results:
            raise ValueError(f"duplicate {expected_agent} calibration result: {task_id}")
        error_type = str(_nested(raw, "exception_info", "exception_type") or "")
        verifier_executed = bool(
            raw.get("verifier_result") or _nested(raw, "verifier", "started_at")
        )
        results[task_id] = CalibrationResult(
            task_id=task_id,
            reward=_reward(raw),
            verifier_executed=verifier_executed,
            error_type=error_type,
        )
    return results


def _task_structure_reasons(task_path: Path, task_id: str) -> list[str]:
    reasons: list[str] = []
    required = ("task.toml", "instruction.md", "environment", "tests", "solution")
    for name in required:
        if not (task_path / name).exists():
            reasons.append(f"missing required task path: {name}")
    task_toml = task_path / "task.toml"
    if task_toml.is_file():
        with task_toml.open("rb") as stream:
            metadata = tomllib.load(stream)
        declared_name = _nested(metadata, "task", "name")
        if declared_name != task_id:
            reasons.append("task.toml name does not match evaluation task id")
    instruction = task_path / "instruction.md"
    if instruction.is_file() and not instruction.read_text(encoding="utf-8").strip():
        reasons.append("instruction.md is empty")
    return reasons


def admit(
    config: EvaluationConfig,
    tasks_path: Path,
    oracle_job_dir: Path,
    nop_job_dir: Path,
    output: Path,
) -> dict[str, Any]:
    manifest_path = dataset_manifest_path(config)
    manifest = validate_local_dataset(config, tasks_path, manifest_path)
    manifest_tasks = {task.name: task.digest for task in manifest.tasks}
    oracle = _calibration_results(oracle_job_dir, "oracle")
    nop = _calibration_results(nop_job_dir, "nop")

    unknown_oracle = sorted(set(oracle) - set(config.tasks))
    unknown_nop = sorted(set(nop) - set(config.tasks))
    if unknown_oracle or unknown_nop:
        raise ValueError(
            "calibration contains unknown tasks: "
            + ", ".join(unknown_oracle + unknown_nop)
        )

    task_results: list[TaskAdmission] = []
    for task_id in config.tasks:
        reasons = _task_structure_reasons(tasks_path / task_id.rsplit("/", 1)[-1], task_id)
        oracle_result = oracle.get(task_id)
        nop_result = nop.get(task_id)
        if oracle_result is None:
            reasons.append("missing oracle calibration")
        elif not oracle_result.verifier_executed or oracle_result.reward is None:
            reasons.append("oracle verifier did not produce a trusted reward")
        elif oracle_result.reward < 1.0:
            reasons.append("oracle did not pass")
        if nop_result is None:
            reasons.append("missing nop calibration")
        elif not nop_result.verifier_executed or nop_result.reward is None:
            reasons.append("nop verifier did not produce a trusted reward")
        elif nop_result.reward >= 1.0:
            reasons.append("nop unexpectedly passed")
        task_results.append(
            TaskAdmission(
                task_id=task_id,
                task_digest=manifest_tasks[task_id],
                status="ADMITTED" if not reasons else "REJECTED",
                oracle_reward=None if oracle_result is None else oracle_result.reward,
                nop_reward=None if nop_result is None else nop_result.reward,
                verifier_selected=None,
                verifier_discovered=None,
                verifier_ignored=None,
                reasons=tuple(reasons),
            )
        )

    status = "ADMITTED" if all(task.status == "ADMITTED" for task in task_results) else "REJECTED"
    report: dict[str, Any] = {
        "schemaVersion": 1,
        "evalId": config.id,
        "dataset": config.dataset,
        "manifestDigest": f"sha256:{manifest.compute_content_hash()}",
        "status": status,
        "tasks": [asdict(task) for task in task_results],
        "manualReviewFocus": [
            "Confirm each public task contract is compatible with its verifier.",
            "Confirm the verifier runs the intended test set; test counts are unavailable "
            "unless the verifier emits structured evidence.",
            "Confirm dependencies and network policy match the frozen task environment.",
        ],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if status != "ADMITTED":
        rejected = ", ".join(task.task_id for task in task_results if task.status == "REJECTED")
        raise ValueError(f"dataset admission rejected tasks: {rejected}")
    return report
