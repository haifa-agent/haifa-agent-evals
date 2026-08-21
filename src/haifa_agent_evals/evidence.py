from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

_REQUIRED_TABLES = {"run", "step", "tool_call", "tool_journal", "runtime_event"}
_TERMINAL_RUN_STATUSES = {"COMPLETED", "FAILED", "CANCELLED", "TIMEOUT"}
_FILE_MUTATORS = {
    "file.create",
    "file.delete",
    "file.move",
    "file.patch",
    "file.write",
}


@dataclass(frozen=True)
class EvidenceSummary:
    valid: bool
    run_id: str | None
    run_status: str | None
    sqlite_integrity: bool
    trace_events: int
    transcript_events: int
    model_attempts: int | None
    workspace_changed: bool | None
    tool_outcome_unknown: bool | None
    failure_stage: str
    failure_code: str
    issues: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _jsonl(path: Path, label: str, issues: list[str]) -> list[dict[str, Any]]:
    if not path.is_file() or path.stat().st_size == 0:
        issues.append(f"{label} is missing or empty")
        return []
    events: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                issues.append(f"{label} has invalid JSON at line {line_number}")
                continue
            if not isinstance(value, dict):
                issues.append(f"{label} line {line_number} is not an object")
                continue
            events.append(value)
    if not events:
        issues.append(f"{label} has no events")
    return events


def _sqlite_summary(
    path: Path, issues: list[str]
) -> tuple[set[str], str | None, str | None, int | None, bool | None]:
    if not path.is_file() or path.stat().st_size == 0:
        issues.append("SQLite evidence is missing or empty")
        return set(), None, None, None, None
    try:
        connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro&immutable=1", uri=True)
    except sqlite3.Error:
        issues.append("SQLite evidence cannot be opened read-only")
        return set(), None, None, None, None
    try:
        quick_check = connection.execute("PRAGMA quick_check").fetchone()
        if quick_check is None or quick_check[0] != "ok":
            issues.append("SQLite quick_check failed")
        foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
        if foreign_keys:
            issues.append("SQLite foreign key check failed")
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        missing_tables = sorted(_REQUIRED_TABLES - tables)
        if missing_tables:
            issues.append(f"SQLite required tables are missing: {', '.join(missing_tables)}")
            return tables, None, None, None, None
        runs = connection.execute(
            "SELECT run_id, status, usage_model_calls FROM run WHERE parent_run_id IS NULL"
        ).fetchall()
        if len(runs) != 1:
            issues.append("SQLite must contain exactly one root run")
            return tables, None, None, None, None
        run_id, run_status, model_attempts = runs[0]
        if run_status not in _TERMINAL_RUN_STATUSES:
            issues.append(f"SQLite root run is not terminal: {run_status}")
        nonterminal_tools = connection.execute(
            "SELECT COUNT(*) FROM tool_call WHERE status NOT IN "
            "('COMPLETED', 'FAILED', 'DENIED', 'CANCELLED', 'TIMEOUT')"
        ).fetchone()[0]
        if nonterminal_tools:
            issues.append(f"SQLite has {nonterminal_tools} nonterminal tool calls")
        nonterminal_journal = connection.execute(
            "SELECT COUNT(*) FROM tool_journal WHERE state NOT IN "
            "('COMPLETED', 'FAILED', 'OUTCOME_UNKNOWN')"
        ).fetchone()[0]
        if nonterminal_journal:
            issues.append(f"SQLite has {nonterminal_journal} nonterminal journal entries")
        outcome_unknown = bool(
            connection.execute(
                "SELECT COUNT(*) FROM tool_journal WHERE state = 'OUTCOME_UNKNOWN'"
            ).fetchone()[0]
        )
        return tables, str(run_id), str(run_status), int(model_attempts), outcome_unknown
    except sqlite3.Error:
        issues.append("SQLite evidence query failed")
        return set(), None, None, None, None
    finally:
        connection.close()


def _failure(events: list[dict[str, Any]]) -> tuple[str, str]:
    for event in reversed(events):
        if event.get("operation") != "runtime.error":
            continue
        attributes = event.get("attributes")
        if not isinstance(attributes, dict):
            return "runtime", "RUNTIME_ERROR"
        code = str(attributes.get("errorCode") or "RUNTIME_ERROR")
        if code.startswith("MODEL_"):
            return "model", code
        if code.startswith("TOOL_"):
            return "tool", code
        if code.startswith("COMPLETION_"):
            return "completion", code
        return "runtime", code
    return "", ""


def _workspace_changed(events: list[dict[str, Any]]) -> bool | None:
    tools: dict[str, str] = {}
    execution_run_succeeded = False
    for event in events:
        tool_call_id = event.get("toolCallId")
        attributes = event.get("attributes")
        if not isinstance(tool_call_id, str) or not isinstance(attributes, dict):
            continue
        if event.get("operation") == "tool.execute":
            tool_name = attributes.get("toolName")
            if isinstance(tool_name, str):
                tools[tool_call_id] = tool_name
        elif event.get("operation") == "tool.persisted":
            if attributes.get("successful") is True and tools.get(tool_call_id) in _FILE_MUTATORS:
                return True
            if attributes.get("successful") is True and tools.get(tool_call_id) == "execution.run":
                execution_run_succeeded = True
    return None if execution_run_succeeded else False


def inspect_haifa_evidence(trial_dir: Path) -> EvidenceSummary:
    issues: list[str] = []
    agent_dir = trial_dir / "agent"
    database = agent_dir / "haifa-runtime.db"
    tables, run_id, run_status, model_attempts, outcome_unknown = _sqlite_summary(database, issues)
    sqlite_integrity = bool(tables >= _REQUIRED_TABLES and run_id)

    trace_events = _jsonl(agent_dir / "haifa-trace.jsonl", "Haifa trace", issues)
    trace_run_ids = {event.get("runId") for event in trace_events if event.get("runId")}
    if run_id and trace_run_ids != {run_id}:
        issues.append("Haifa trace Run ID does not match SQLite")

    transcript_dir = agent_dir / "haifa-transcripts"
    transcript_files = sorted(transcript_dir.glob("*.jsonl")) if transcript_dir.is_dir() else []
    if not transcript_files:
        issues.append("Haifa transcript JSONL is missing")
    transcript_count = 0
    for transcript in transcript_files:
        events = _jsonl(transcript, f"transcript {transcript.name}", issues)
        transcript_count += len(events)
        run_ids = {event.get("runId") for event in events if event.get("runId")}
        if run_id and run_ids != {run_id}:
            issues.append(f"transcript {transcript.name} Run ID does not match SQLite")
        sequences = [event.get("sequence") for event in events]
        if not all(isinstance(sequence, int) for sequence in sequences):
            issues.append(f"transcript {transcript.name} has invalid sequence values")
        elif sequences != sorted(sequences) or len(sequences) != len(set(sequences)):
            issues.append(f"transcript {transcript.name} sequence is not strictly increasing")
        terminal_events = {"run.cancelled", "run.completed", "run.failed", "run.timeout"}
        if events and events[-1].get("eventType") not in terminal_events:
            issues.append(f"transcript {transcript.name} has no terminal Run event")

    failure_stage, failure_code = _failure(trace_events)
    workspace_changed = _workspace_changed(trace_events) if trace_events else None
    return EvidenceSummary(
        valid=not issues,
        run_id=run_id,
        run_status=run_status,
        sqlite_integrity=sqlite_integrity,
        trace_events=len(trace_events),
        transcript_events=transcript_count,
        model_attempts=model_attempts,
        workspace_changed=workspace_changed,
        tool_outcome_unknown=outcome_unknown,
        failure_stage=failure_stage,
        failure_code=failure_code,
        issues=tuple(issues),
    )
