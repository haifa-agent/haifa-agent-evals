from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

_EVAL_FIELDS = {"id", "dataset", "tasks", "attempts", "timeoutMinutes", "candidates"}
_CANDIDATE_FIELDS = {"id", "agent", "model", "provider"}
_FLOATING_DATASET_REFS = {"latest", "main", "head"}
_SAFE_ID_CHARACTERS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")


@dataclass(frozen=True)
class Candidate:
    id: str
    agent: str
    model: str
    provider: str | None = None

    def resolved_provider(self) -> str | None:
        if self.provider is not None:
            return self.provider
        if self.id in {"haifa", "aider"}:
            return "deepseek"
        return None


@dataclass(frozen=True)
class EvaluationConfig:
    id: str
    dataset: str
    tasks: tuple[str, ...]
    attempts: int
    timeout_minutes: int
    candidates: tuple[Candidate, ...]


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a mapping")
    return value


def _required_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def _safe_id(value: Any, label: str) -> str:
    resolved = _required_string(value, label)
    if any(character not in _SAFE_ID_CHARACTERS for character in resolved):
        raise ValueError(f"{label} must contain only letters, numbers, '-' or '_'")
    return resolved


def load_config(path: Path) -> EvaluationConfig:
    with path.open(encoding="utf-8") as stream:
        raw = _mapping(yaml.safe_load(stream), "evaluation config")

    unknown = set(raw) - _EVAL_FIELDS
    missing = _EVAL_FIELDS - set(raw)
    if unknown:
        raise ValueError(f"unknown evaluation fields: {', '.join(sorted(unknown))}")
    if missing:
        raise ValueError(f"missing evaluation fields: {', '.join(sorted(missing))}")

    dataset = _required_string(raw["dataset"], "dataset")
    if "@" not in dataset:
        raise ValueError("dataset must include an exact @version")
    version = dataset.rsplit("@", 1)[1].lower()
    if not version or version in _FLOATING_DATASET_REFS:
        raise ValueError("dataset must not use a floating version")

    task_values = raw["tasks"]
    if not isinstance(task_values, list) or not task_values:
        raise ValueError("tasks must be a non-empty list")
    tasks = tuple(_required_string(value, "task id") for value in task_values)
    if len(tasks) != len(set(tasks)):
        raise ValueError("task ids must be unique")

    candidate_values = raw["candidates"]
    if not isinstance(candidate_values, list) or not candidate_values:
        raise ValueError("candidates must be a non-empty list")
    candidates: list[Candidate] = []
    for index, value in enumerate(candidate_values):
        candidate = _mapping(value, f"candidate[{index}]")
        candidate_unknown = set(candidate) - _CANDIDATE_FIELDS
        candidate_missing = {"id", "agent", "model"} - set(candidate)
        if candidate_unknown:
            raise ValueError(f"unknown candidate fields: {', '.join(sorted(candidate_unknown))}")
        if candidate_missing:
            raise ValueError(f"missing candidate fields: {', '.join(sorted(candidate_missing))}")
        candidates.append(
            Candidate(
                id=_safe_id(candidate["id"], "candidate id"),
                agent=_required_string(candidate["agent"], "candidate agent"),
                model=_required_string(candidate["model"], "candidate model"),
                provider=(
                    _safe_id(candidate["provider"], "candidate provider")
                    if "provider" in candidate
                    else None
                ),
            )
        )
    candidate_ids = [candidate.id for candidate in candidates]
    if len(candidate_ids) != len(set(candidate_ids)):
        raise ValueError("candidate ids must be unique")

    attempts = raw["attempts"]
    timeout = raw["timeoutMinutes"]
    if attempts != 1 or isinstance(attempts, bool):
        raise ValueError("MVP supports exactly one attempt per candidate/task")
    if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout < 1:
        raise ValueError("timeoutMinutes must be a positive integer")

    return EvaluationConfig(
        id=_safe_id(raw["id"], "id"),
        dataset=dataset,
        tasks=tasks,
        attempts=attempts,
        timeout_minutes=timeout,
        candidates=tuple(candidates),
    )
