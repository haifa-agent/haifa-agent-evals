from pathlib import Path

from haifa_agent_evals.verifier_counts import extract_verifier_counts


def _output(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "test-stdout.txt"
    path.write_text(text, encoding="utf-8")
    return path


def test_extracts_rust_selected_and_filtered_tests(tmp_path: Path) -> None:
    counts = extract_verifier_counts(
        _output(
            tmp_path,
            "test result: ok. 1 passed; 0 failed; 0 ignored; 0 measured; 14 filtered out\n",
        )
    )

    assert counts is not None
    assert counts.selected == 1
    assert counts.discovered == 15
    assert counts.ignored == 14


def test_extracts_pytest_summary(tmp_path: Path) -> None:
    counts = extract_verifier_counts(
        _output(tmp_path, "======= 1 failed, 14 passed, 2 skipped, 3 deselected in 0.12s =======\n")
    )

    assert counts is not None
    assert counts.selected == 17
    assert counts.discovered == 20
    assert counts.ignored == 5


def test_rust_ignored_tests_are_discovered_but_not_selected(tmp_path: Path) -> None:
    counts = extract_verifier_counts(
        _output(
            tmp_path,
            "test result: ok. 1 passed; 0 failed; 17 ignored; 0 measured; 0 filtered out\n",
        )
    )

    assert counts is not None
    assert counts.selected == 1
    assert counts.discovered == 18
    assert counts.ignored == 17


def test_returns_unknown_for_unstructured_output(tmp_path: Path) -> None:
    assert extract_verifier_counts(_output(tmp_path, "all good\n")) is None
