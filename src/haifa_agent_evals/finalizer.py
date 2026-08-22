from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Any

from haifa_agent_evals.collector import collect
from haifa_agent_evals.config import load_config
from haifa_agent_evals.dataset import dataset_manifest_path
from haifa_agent_evals.evidence import inspect_haifa_evidence
from haifa_agent_evals.reporter import report
from haifa_agent_evals.runner import config_sha256

_TEXT_SUFFIXES = {
    ".csv",
    ".json",
    ".jsonl",
    ".log",
    ".md",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}
_RAW_SAFETY_PATTERNS = {
    "secret-token": re.compile(r"sk-[A-Za-z0-9_-]{16,}"),
    "credential-value": re.compile(
        r"(?i)(?:api[_-]?key|authorization)[\"']?\s*[:=]\s*[\"']?"
        r"(?:bearer\s+)?(?!\$\{|redacted)[A-Za-z0-9_-]{16,}"
    ),
    "reasoning-content": re.compile(r'(?i)"reasoning(?:_content|Content)"\s*:'),
    "provider-response": re.compile(r'(?i)"raw(?:_provider)?_response"\s*:'),
}
_PUBLISHED_PATH_PATTERNS = {
    "windows-host-path": re.compile(r"[A-Za-z]:\\"),
    "unix-user-path": re.compile(r"/(?:Users|home)/[^/\s]+/"),
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_manifest(
    path: Path,
    eval_id: str,
    dataset: str,
    config_digest: str,
) -> dict[str, Any]:
    if not path.is_file():
        raise ValueError("run manifest is missing")
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("run manifest is not an object")
    if raw.get("evalId") != eval_id or raw.get("dataset") != dataset:
        raise ValueError("run manifest identity does not match config")
    if raw.get("configSha256") != config_digest:
        raise ValueError("run manifest config digest does not match")
    if raw.get("runStatus") != "HARBOR_FINISHED":
        raise ValueError("run manifest is not ready for finalization")
    return raw


def _copy_input(path: Path | None, inputs: Path, name: str) -> None:
    if path is not None and path.is_file():
        if _is_link(path):
            raise ValueError(f"{name} input must not be a symbolic link")
        shutil.copy2(path, inputs / name)


def _is_link(path: Path) -> bool:
    return path.is_symlink() or path.is_junction()


def _reject_symlinks(root: Path) -> None:
    if _is_link(root) or any(_is_link(path) for path in root.rglob("*")):
        raise ValueError("Harbor job archive contains a symbolic link or junction")


def _validate_evidence_digest(
    manifest: dict[str, Any],
    field: str,
    path: Path | None,
    label: str,
) -> None:
    expected = manifest.get(field)
    if expected is None:
        return
    if path is None or not path.is_file():
        raise ValueError(f"{label} evidence required by run manifest is missing")
    if _sha256(path) != expected:
        raise ValueError(f"{label} evidence digest does not match run manifest")


def _scan_files(
    root: Path,
    patterns: dict[str, re.Pattern[str]],
    scope: str,
) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        if path.suffix.lower() not in _TEXT_SUFFIXES:
            continue
        try:
            matched: set[str] = set()
            with path.open(encoding="utf-8", errors="replace") as stream:
                for line in stream:
                    for rule, pattern in patterns.items():
                        if rule not in matched and pattern.search(line):
                            matched.add(rule)
        except OSError:
            findings.append(
                {
                    "scope": scope,
                    "file": path.relative_to(root).as_posix(),
                    "rule": "read-error",
                }
            )
            continue
        for rule in sorted(matched):
            findings.append(
                {
                    "scope": scope,
                    "file": path.relative_to(root).as_posix(),
                    "rule": rule,
                }
            )
    return findings


def _write_checksums(archive_dir: Path, output: Path) -> int:
    files = sorted(
        path
        for path in archive_dir.rglob("*")
        if path.is_file() and path.resolve() != output.resolve()
    )
    lines = [f"{_sha256(path)}  {path.relative_to(archive_dir).as_posix()}" for path in files]
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(files)


def finalize(
    config_path: Path,
    job_dir: Path,
    archive_dir: Path,
    *,
    run_manifest_path: Path | None = None,
    admission_path: Path | None = None,
    preflight_path: Path | None = None,
) -> dict[str, Any]:
    if not job_dir.is_dir():
        raise ValueError("Harbor job directory does not exist")
    if archive_dir.exists():
        raise ValueError("archive directory already exists; finalization never overwrites")
    resolved_job = job_dir.resolve()
    resolved_archive = archive_dir.resolve()
    if resolved_archive.is_relative_to(resolved_job):
        raise ValueError("archive directory must not be inside the Harbor job directory")
    _reject_symlinks(job_dir)

    config = load_config(config_path)
    manifest_path = run_manifest_path or job_dir.parent / f"{job_dir.name}-run-manifest.json"
    manifest = _read_manifest(manifest_path, config.id, config.dataset, config_sha256(config))
    expected_trials = {
        (candidate.id, task, attempt)
        for candidate in config.candidates
        for task in config.tasks
        for attempt in range(1, config.attempts + 1)
    }
    manifest_trials = {
        (trial.get("candidate"), trial.get("taskId"), trial.get("attempt"))
        for trial in manifest.get("plannedTrials", [])
        if isinstance(trial, dict)
    }
    if manifest_trials != expected_trials:
        raise ValueError("run manifest planned trial matrix does not match config")
    resolved_admission = (
        admission_path or Path("work") / "gates" / "admissions" / f"{config.id}.json"
    )
    resolved_preflight = preflight_path or job_dir.parent / f"{job_dir.name}-preflight.json"
    _validate_evidence_digest(manifest, "admissionSha256", resolved_admission, "admission")
    _validate_evidence_digest(manifest, "preflightSha256", resolved_preflight, "preflight")

    inputs = archive_dir / "inputs"
    archived_job = archive_dir / "jobs" / "harbor-job"
    reports = archive_dir / "reports"
    integrity = archive_dir / "integrity"
    inputs.mkdir(parents=True)
    reports.mkdir(parents=True)
    integrity.mkdir(parents=True)
    _copy_input(config_path, inputs, "eval.yaml")
    _copy_input(dataset_manifest_path(config), inputs, "dataset.toml")
    _copy_input(manifest_path, inputs, "run-manifest.json")
    _copy_input(resolved_admission, inputs, "admission.json")
    _copy_input(resolved_preflight, inputs, "preflight.json")
    shutil.copytree(job_dir, archived_job)

    results_path = reports / "results.csv"
    results = collect(
        archived_job,
        results_path,
        config=config,
        require_evidence=True,
    )
    comparison_path = report(results_path, reports / "comparison.md", config)

    evidence: list[dict[str, Any]] = []
    for result in results:
        if result.candidate != "haifa":
            continue
        summary = inspect_haifa_evidence(archived_job / result.trial_path)
        evidence.append(
            {
                "candidate": result.candidate,
                "taskId": result.task_id,
                "attempt": result.attempt,
                "trialPath": result.trial_path,
                "summary": summary.as_dict(),
            }
        )

    findings = _scan_files(archived_job, _RAW_SAFETY_PATTERNS, "private-evidence")
    findings.extend(_scan_files(reports, _PUBLISHED_PATH_PATTERNS, "published-report"))
    findings.extend(_scan_files(inputs, _PUBLISHED_PATH_PATTERNS, "published-input"))
    evidence_issues = sum(len(item["summary"]["issues"]) for item in evidence)
    invalid_trials = sum(result.trial_validity != "VALID" for result in results)
    status = (
        "COMPLETE" if not findings and not evidence_issues and not invalid_trials else "BLOCKED"
    )
    finalization: dict[str, Any] = {
        "schemaVersion": 1,
        "evalId": config.id,
        "runId": manifest["runId"],
        "dataset": config.dataset,
        "status": status,
        "plannedTrials": len(manifest["plannedTrials"]),
        "observedTrials": len(results),
        "invalidTrials": invalid_trials,
        "evidenceIssues": evidence_issues,
        "safetyFindings": findings,
        "cleanup": {
            "status": "MANUAL_REVIEW",
            "detail": (
                "Harbor delete policy is configured; verify no container or child process remains."
            ),
        },
        "evidence": evidence,
        "reports": [comparison_path.name, results_path.name],
    }
    finalization_path = integrity / "finalization.json"
    finalization_path.write_text(json.dumps(finalization, indent=2) + "\n", encoding="utf-8")
    checksum_count = _write_checksums(archive_dir, integrity / "SHA256SUMS.txt")
    finalization["checksummedFiles"] = checksum_count
    finalization_path.write_text(json.dumps(finalization, indent=2) + "\n", encoding="utf-8")
    _write_checksums(archive_dir, integrity / "SHA256SUMS.txt")
    return finalization
