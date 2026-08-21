from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from haifa_agent_evals.config import EvaluationConfig
from haifa_agent_evals.dataset import dataset_manifest_path, validate_local_dataset
from haifa_agent_evals.runner import _default_haifa_jar

EXPECTED_HARBOR_VERSION = "0.20.0"
MINIMUM_FREE_BYTES = 5 * 1024 * 1024 * 1024


@dataclass(frozen=True)
class DoctorCheck:
    name: str
    status: str
    detail: str


CommandProbe = Callable[[Sequence[str]], bool]
Which = Callable[[str], str | None]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _default_probe(command: Sequence[str]) -> bool:
    try:
        completed = subprocess.run(  # noqa: S603
            list(command),
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


def _admission_check(path: Path, config: EvaluationConfig) -> tuple[DoctorCheck, str | None]:
    if not path.is_file():
        return DoctorCheck("admission", "FAIL", "admission evidence is missing"), None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return DoctorCheck("admission", "FAIL", "admission evidence is unreadable"), None
    if not isinstance(raw, dict):
        return DoctorCheck("admission", "FAIL", "admission evidence is not an object"), None
    if raw.get("status") != "ADMITTED":
        return DoctorCheck("admission", "FAIL", "dataset is not admitted"), None
    if raw.get("evalId") != config.id or raw.get("dataset") != config.dataset:
        return DoctorCheck("admission", "FAIL", "admission identity does not match config"), None
    dataset_digest = config.dataset.rsplit("@", 1)[1]
    if raw.get("manifestDigest") != dataset_digest:
        return DoctorCheck("admission", "FAIL", "admission manifest digest does not match"), None
    task_records = raw.get("tasks")
    if not isinstance(task_records, list):
        return DoctorCheck("admission", "FAIL", "admission task evidence is missing"), None
    admitted_tasks = {
        record.get("task_id")
        for record in task_records
        if isinstance(record, dict) and record.get("status") == "ADMITTED"
    }
    if admitted_tasks != set(config.tasks):
        return DoctorCheck("admission", "FAIL", "admission task set does not match"), None
    return DoctorCheck("admission", "PASS", "matching admitted dataset evidence"), _sha256(path)


def doctor(
    config: EvaluationConfig,
    tasks_path: Path,
    admission_path: Path,
    output: Path,
    *,
    jar_path: Path | None = None,
    container_cli: str | None = None,
    environment: Mapping[str, str] | None = None,
    command_probe: CommandProbe = _default_probe,
    which: Which = shutil.which,
    free_bytes: int | None = None,
    harbor_version: str | None = None,
) -> dict[str, object]:
    checks: list[DoctorCheck] = []
    admission_check, admission_digest = _admission_check(admission_path, config)
    checks.append(admission_check)

    try:
        validate_local_dataset(config, tasks_path, dataset_manifest_path(config))
        checks.append(DoctorCheck("dataset", "PASS", "manifest and task digests match"))
    except (OSError, ValueError) as error:
        checks.append(DoctorCheck("dataset", "FAIL", str(error)))

    actual_harbor = harbor_version
    if actual_harbor is None:
        try:
            actual_harbor = version("harbor")
        except PackageNotFoundError:
            actual_harbor = "unavailable"
    checks.append(
        DoctorCheck(
            "harbor",
            "PASS" if actual_harbor == EXPECTED_HARBOR_VERSION else "FAIL",
            f"version {actual_harbor}; expected {EXPECTED_HARBOR_VERSION}",
        )
    )

    resolved_container = container_cli or which("docker") or which("podman")
    if resolved_container and command_probe([resolved_container, "info"]):
        checks.append(DoctorCheck("container", "PASS", "container backend is reachable"))
    else:
        checks.append(DoctorCheck("container", "FAIL", "container backend is unavailable"))

    requires_haifa = any(candidate.id == "haifa" for candidate in config.candidates)
    resolved_jar = jar_path or Path(
        (environment or os.environ).get("HAIFA_EVAL_JAR_PATH", str(_default_haifa_jar()))
    )
    java = which("java")
    if requires_haifa:
        if not resolved_jar.is_file():
            checks.append(DoctorCheck("haifa-jar", "FAIL", "configured JAR is missing"))
        elif not java or not command_probe([java, "-jar", str(resolved_jar), "--help"]):
            checks.append(DoctorCheck("haifa-jar", "FAIL", "JAR --help smoke check failed"))
        else:
            checks.append(
                DoctorCheck(
                    "haifa-jar",
                    "PASS",
                    f"readable executable JAR; sha256:{_sha256(resolved_jar)}",
                )
            )
    else:
        checks.append(DoctorCheck("haifa-jar", "SKIP", "evaluation has no Haifa candidate"))

    current_environment = environment or os.environ
    required_credentials = (
        {"DEEPSEEK_API_KEY"}
        if any(candidate.id in {"haifa", "aider"} for candidate in config.candidates)
        else set()
    )
    missing_credentials = sorted(
        name for name in required_credentials if not current_environment.get(name)
    )
    checks.append(
        DoctorCheck(
            "credentials",
            "FAIL" if missing_credentials else "PASS",
            (
                f"missing variables: {', '.join(missing_credentials)}"
                if missing_credentials
                else "required credential variables are present"
            ),
        )
    )

    available = free_bytes
    if available is None:
        available = shutil.disk_usage(tasks_path).free if tasks_path.exists() else 0
    checks.append(
        DoctorCheck(
            "disk",
            "PASS" if available >= MINIMUM_FREE_BYTES else "FAIL",
            f"{available} bytes free; minimum {MINIMUM_FREE_BYTES}",
        )
    )
    checks.append(
        DoctorCheck(
            "python",
            "PASS" if sys.version_info[:2] == (3, 12) else "FAIL",
            f"Python {sys.version_info.major}.{sys.version_info.minor}",
        )
    )

    status = "READY" if all(check.status != "FAIL" for check in checks) else "BLOCKED"
    report: dict[str, object] = {
        "schemaVersion": 1,
        "evalId": config.id,
        "dataset": config.dataset,
        "status": status,
        "admissionSha256": admission_digest,
        "checks": [asdict(check) for check in checks],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report
