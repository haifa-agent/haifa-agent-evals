from __future__ import annotations

import importlib.metadata
import re
import sys
from pathlib import Path


def canonical_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def locked_packages(path: Path) -> dict[str, str]:
    packages: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        name, separator, version = line.partition("==")
        if not separator or not name or not version:
            raise ValueError(f"invalid locked requirement: {line}")
        packages[canonical_name(name)] = version
    return packages


def installed_packages() -> dict[str, str]:
    return {
        canonical_name(distribution.metadata["Name"]): distribution.version
        for distribution in importlib.metadata.distributions()
        if distribution.metadata["Name"]
    }


expected = locked_packages(Path(sys.argv[1]))
actual = installed_packages()
if expected != actual:
    missing = sorted(set(expected) - set(actual))
    unexpected = sorted(set(actual) - set(expected))
    changed = sorted(
        name for name in expected.keys() & actual.keys() if expected[name] != actual[name]
    )
    raise SystemExit(
        f"Aider dependency lock mismatch: missing={missing}, "
        f"unexpected={unexpected}, changed={changed}"
    )

