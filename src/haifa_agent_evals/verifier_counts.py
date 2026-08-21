from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_RUST = re.compile(
    r"test result: (?:ok|FAILED)\.\s+"
    r"(?P<passed>\d+) passed;\s+"
    r"(?P<failed>\d+) failed;\s+"
    r"(?P<ignored>\d+) ignored;\s+"
    r"(?P<measured>\d+) measured;\s+"
    r"(?P<filtered>\d+) filtered out"
)
_JEST = re.compile(r"^Tests:\s+(?P<body>.+)$", re.MULTILINE)
_GRADLE = re.compile(
    r"(?P<total>\d+) tests? completed"
    r"(?:,\s+(?P<failed>\d+) failed)?"
    r"(?:,\s+(?P<skipped>\d+) skipped)?"
)
_COUNT = re.compile(
    r"(?P<count>\d+)\s+"
    r"(?P<label>passed|failed|skipped|deselected|errors?|total|filtered out)"
)


@dataclass(frozen=True)
class VerifierCounts:
    selected: int
    discovered: int
    ignored: int


def _token_counts(text: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for match in _COUNT.finditer(text):
        label = match.group("label")
        counts[label] = counts.get(label, 0) + int(match.group("count"))
    return counts


def extract_verifier_counts(path: Path) -> VerifierCounts | None:
    if not path.is_file() or path.stat().st_size == 0:
        return None
    text = _ANSI.sub("", path.read_text(encoding="utf-8", errors="replace"))

    rust_matches = list(_RUST.finditer(text))
    if rust_matches:
        selected = sum(
            int(match.group("passed")) + int(match.group("failed")) + int(match.group("measured"))
            for match in rust_matches
        )
        filtered = sum(int(match.group("filtered")) for match in rust_matches)
        explicitly_ignored = sum(int(match.group("ignored")) for match in rust_matches)
        ignored = explicitly_ignored + filtered
        return VerifierCounts(selected=selected, discovered=selected + ignored, ignored=ignored)

    jest_matches = list(_JEST.finditer(text))
    if jest_matches:
        counts = _token_counts(jest_matches[-1].group("body"))
        total = counts.get("total")
        if total is not None:
            skipped = counts.get("skipped", 0)
            return VerifierCounts(selected=total, discovered=total, ignored=skipped)

    gradle_matches = list(_GRADLE.finditer(text))
    if gradle_matches:
        match = gradle_matches[-1]
        total = int(match.group("total"))
        skipped = int(match.group("skipped") or 0)
        return VerifierCounts(selected=total, discovered=total, ignored=skipped)

    pytest_lines = [
        line
        for line in text.splitlines()
        if " in " in line and (" passed" in line or " failed" in line)
    ]
    if pytest_lines:
        counts = _token_counts(pytest_lines[-1])
        selected = sum(
            counts.get(label, 0) for label in ("passed", "failed", "skipped", "error", "errors")
        )
        deselected = counts.get("deselected", 0)
        if selected:
            return VerifierCounts(
                selected=selected,
                discovered=selected + deselected,
                ignored=counts.get("skipped", 0) + deselected,
            )
    return None
