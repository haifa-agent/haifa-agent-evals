from __future__ import annotations

import csv
import json
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from haifa_agent_evals.config import EvaluationConfig
from haifa_agent_evals.evidence import inspect_haifa_evidence
from haifa_agent_evals.verifier_counts import extract_verifier_counts

CSV_FIELDS = (
    "eval_id",
    "candidate",
    "model",
    "agent_version",
    "task_id",
    "language",
    "attempt",
    "status",
    "trial_validity",
    "agent_clean_exit",
    "workspace_changed",
    "verifier_executed",
    "verifier_selected",
    "verifier_discovered",
    "verifier_ignored",
    "failure_stage",
    "failure_code",
    "model_attempts",
    "tool_outcome_unknown",
    "reward",
    "duration_seconds",
    "exit_code",
    "error_type",
    "trial_path",
)


@dataclass(frozen=True)
class Result:
    eval_id: str
    candidate: str
    model: str
    agent_version: str
    task_id: str
    language: str
    attempt: int
    status: str
    trial_validity: str
    agent_clean_exit: bool | None
    workspace_changed: bool | None
    verifier_executed: bool
    verifier_selected: int | None
    verifier_discovered: int | None
    verifier_ignored: int | None
    failure_stage: str
    failure_code: str
    model_attempts: int | None
    tool_outcome_unknown: bool | None
    reward: float | None
    duration_seconds: float | None
    exit_code: int | None
    error_type: str
    trial_path: str

    @staticmethod
    def _boolean(value: bool | None) -> str:
        return "unknown" if value is None else str(value).lower()

    def as_row(self) -> dict[str, str | int | float]:
        return {
            "eval_id": self.eval_id,
            "candidate": self.candidate,
            "model": self.model,
            "agent_version": self.agent_version,
            "task_id": self.task_id,
            "language": self.language,
            "attempt": self.attempt,
            "status": self.status,
            "trial_validity": self.trial_validity,
            "agent_clean_exit": self._boolean(self.agent_clean_exit),
            "workspace_changed": self._boolean(self.workspace_changed),
            "verifier_executed": self._boolean(self.verifier_executed),
            "verifier_selected": ("" if self.verifier_selected is None else self.verifier_selected),
            "verifier_discovered": (
                "" if self.verifier_discovered is None else self.verifier_discovered
            ),
            "verifier_ignored": "" if self.verifier_ignored is None else self.verifier_ignored,
            "failure_stage": self.failure_stage,
            "failure_code": self.failure_code,
            "model_attempts": "" if self.model_attempts is None else self.model_attempts,
            "tool_outcome_unknown": self._boolean(self.tool_outcome_unknown),
            "reward": "" if self.reward is None else self.reward,
            "duration_seconds": "" if self.duration_seconds is None else self.duration_seconds,
            "exit_code": "" if self.exit_code is None else self.exit_code,
            "error_type": self.error_type,
            "trial_path": self.trial_path,
        }


def _read_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path.name}")
    return value


def _nested(value: dict[str, Any], *path: str) -> Any:
    current: Any = value
    for item in path:
        if not isinstance(current, dict):
            return None
        current = current.get(item)
    return current


def _first(*values: Any) -> Any:
    return next((value for value in values if value is not None), None)


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)


def _reward(raw: dict[str, Any]) -> float | None:
    rewards = _nested(raw, "verifier_result", "rewards")
    if not isinstance(rewards, dict) or not rewards:
        return None
    if "reward" in rewards:
        return _float_or_none(rewards["reward"])
    if len(rewards) == 1:
        return _float_or_none(next(iter(rewards.values())))
    raise ValueError("trial has multiple verifier rewards but no canonical 'reward'")


def _duration(raw: dict[str, Any]) -> float | None:
    started = raw.get("started_at")
    finished = raw.get("finished_at")
    if not isinstance(started, str) or not isinstance(finished, str):
        return None
    return (datetime.fromisoformat(finished) - datetime.fromisoformat(started)).total_seconds()


def _language(task_id: str) -> str:
    match = re.search(
        r"(?:polyglot_|coding-smoke-)(cpp|go|java|javascript|python|rust)[_-]",
        task_id,
    )
    return match.group(1) if match else "unknown"


def _agent_version(raw: dict[str, Any], trial_dir: Path, candidate: str) -> str:
    reported = str(_first(_nested(raw, "agent_info", "version"), "")).strip()
    safe_version = r"(?:sha256:[0-9a-f]{64}|v?\d+(?:\.\d+){1,3}(?:[-+][0-9A-Za-z.-]+)?)"
    if re.fullmatch(safe_version, reported):
        return reported.removeprefix("v")
    if candidate == "aider":
        aider_log = trial_dir / "agent" / "aider.txt"
        if aider_log.is_file():
            with aider_log.open(encoding="utf-8", errors="replace") as stream:
                for index, line in enumerate(stream):
                    if index >= 32:
                        break
                    match = re.fullmatch(
                        r"Aider v?(\d+(?:\.\d+){1,3}(?:[-+][0-9A-Za-z.-]+)?)\s*",
                        line,
                    )
                    if match:
                        return match.group(1)
    return "unavailable"


def _verifier_executed(raw: dict[str, Any]) -> bool:
    return bool(raw.get("verifier_result") or _nested(raw, "verifier", "started_at"))


def _failure_stage(raw: dict[str, Any], reward: float | None, error_type: str) -> str:
    if not error_type:
        return "" if reward is not None else "incomplete"
    if reward is not None:
        return "agent"
    if _nested(raw, "verifier", "started_at"):
        return "verifier"
    if _nested(raw, "agent_execution", "started_at"):
        return "agent"
    if _nested(raw, "agent_setup", "started_at"):
        return "agent_setup"
    if _nested(raw, "environment_setup", "started_at"):
        return "environment"
    return "incomplete"


def _trial_validity(reward: float | None, error_type: str, failure_stage: str) -> str:
    if reward is not None:
        return "VALID"
    lowered = error_type.lower()
    if failure_stage == "verifier" or "verifier" in lowered or "rewardfile" in lowered:
        return "INVALID_DATASET"
    infrastructure_markers = ("environment", "container", "docker", "setup")
    if failure_stage in {"environment", "agent_setup"} or any(
        marker in lowered for marker in infrastructure_markers
    ):
        return "INVALID_INFRA"
    return "INCOMPLETE"


def _trial_result(
    job_dir: Path,
    trial_dir: Path,
    eval_id: str,
    require_evidence: bool = False,
) -> Result:
    config = _read_json(trial_dir / "config.json")
    raw = _read_json(trial_dir / "result.json")
    reward = _reward(raw)
    error_type = str(
        _first(
            raw.get("error_type"),
            _nested(raw, "exception_info", "exception_type"),
            "",
        )
    )
    status = "ERROR" if reward is None else ("PASS" if reward >= 1.0 else "FAIL")
    task_id = str(_first(raw.get("task_name"), _nested(config, "task", "name"), "unknown"))
    candidate = str(
        _first(
            _nested(raw, "agent_info", "name"),
            _nested(config, "agent", "name"),
            "unknown",
        )
    )
    model = str(
        _first(
            _nested(config, "agent", "model_name"),
            _nested(raw, "agent_info", "model_info", "name"),
            "unknown",
        )
    )
    agent_version = _agent_version(raw, trial_dir, candidate)
    duration = _duration(raw)
    exit_code = _int_or_none(
        _first(raw.get("exit_code"), _nested(raw, "agent_result", "metadata", "exit_code"))
    )
    verifier_executed = _verifier_executed(raw)
    failure_stage = _failure_stage(raw, reward, error_type)
    trial_validity = _trial_validity(reward, error_type, failure_stage)
    agent_clean_exit = None
    if exit_code is not None:
        agent_clean_exit = exit_code == 0 and not error_type
    workspace_changed = None
    model_attempts = None
    tool_outcome_unknown = None
    evidence_present = (trial_dir / "agent" / "haifa-runtime.db").is_file() or (
        trial_dir / "agent" / "haifa-trace.jsonl"
    ).is_file()
    if candidate == "haifa" and (require_evidence or evidence_present):
        evidence = inspect_haifa_evidence(trial_dir)
        workspace_changed = evidence.workspace_changed
        model_attempts = evidence.model_attempts
        tool_outcome_unknown = evidence.tool_outcome_unknown
        failure_stage = evidence.failure_stage or failure_stage
        failure_code = evidence.failure_code or error_type
        if not evidence.valid:
            trial_validity = "INCOMPLETE"
    else:
        failure_code = error_type
    verifier_counts = extract_verifier_counts(trial_dir / "verifier" / "test-stdout.txt")
    language = _language(task_id)
    attempt = 1
    return Result(
        eval_id=eval_id,
        candidate=candidate,
        model=model,
        agent_version=agent_version,
        task_id=task_id,
        language=language,
        attempt=attempt,
        status=status,
        trial_validity=trial_validity,
        agent_clean_exit=agent_clean_exit,
        workspace_changed=workspace_changed,
        verifier_executed=verifier_executed,
        verifier_selected=None if verifier_counts is None else verifier_counts.selected,
        verifier_discovered=None if verifier_counts is None else verifier_counts.discovered,
        verifier_ignored=None if verifier_counts is None else verifier_counts.ignored,
        failure_stage=failure_stage,
        failure_code=failure_code,
        model_attempts=model_attempts,
        tool_outcome_unknown=tool_outcome_unknown,
        reward=reward,
        duration_seconds=duration,
        exit_code=exit_code,
        error_type=error_type,
        trial_path=trial_dir.relative_to(job_dir).as_posix(),
    )


def _validate_matrix(results: list[Result], config: EvaluationConfig) -> None:
    expected = {
        (candidate.id, task, attempt)
        for candidate in config.candidates
        for task in config.tasks
        for attempt in range(1, config.attempts + 1)
    }
    observed_counts = Counter(
        (result.candidate, result.task_id, result.attempt) for result in results
    )
    observed = set(observed_counts)
    duplicates = sorted(key for key, count in observed_counts.items() if count > 1)
    missing = sorted(expected - observed)
    unknown = sorted(observed - expected)
    failures: list[str] = []
    if missing:
        failures.append(f"missing={missing}")
    if duplicates:
        failures.append(f"duplicate={duplicates}")
    if unknown:
        failures.append(f"unknown={unknown}")
    if failures:
        raise ValueError("trial matrix is incomplete: " + "; ".join(failures))


def collect(
    job_dir: Path,
    output: Path,
    eval_id: str | None = None,
    config: EvaluationConfig | None = None,
    require_evidence: bool = False,
) -> list[Result]:
    if not job_dir.is_dir():
        raise ValueError(f"job directory does not exist: {job_dir}")
    if config is not None and eval_id is not None and config.id != eval_id:
        raise ValueError("eval id does not match the supplied config")
    resolved_eval_id = config.id if config is not None else (eval_id or job_dir.name)
    trial_dirs = sorted(
        result_path.parent
        for result_path in job_dir.rglob("result.json")
        if result_path.parent != job_dir and (result_path.parent / "config.json").is_file()
    )
    if not trial_dirs:
        raise ValueError("no Harbor trial result.json files found")
    results = [
        _trial_result(job_dir, trial_dir, resolved_eval_id, require_evidence)
        for trial_dir in trial_dirs
    ]
    if config is not None:
        _validate_matrix(results, config)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=CSV_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(result.as_row() for result in results)
    return results
