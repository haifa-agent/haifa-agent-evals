from pathlib import Path

import pytest
from harbor.models.dataset.manifest import DatasetManifest

from haifa_agent_evals.config import load_config


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "eval.yaml"
    path.write_text(text, encoding="utf-8")
    return path


BASE = """\
id: smoke
dataset: org/data@v1
tasks: [task-a, task-b]
attempts: 1
timeoutMinutes: 20
candidates:
  - id: first
    agent: aider
    model: provider/model
"""


def test_loads_minimal_config(tmp_path: Path) -> None:
    config = load_config(_write(tmp_path, BASE))
    assert config.id == "smoke"
    assert config.tasks == ("task-a", "task-b")
    assert config.max_retries == 0


def test_loads_one_infrastructure_retry(tmp_path: Path) -> None:
    config = load_config(_write(tmp_path, BASE + "maxRetries: 1\n"))

    assert config.max_retries == 1


@pytest.mark.parametrize("value", ["-1", "2", "true"])
def test_rejects_invalid_infrastructure_retry(tmp_path: Path, value: str) -> None:
    with pytest.raises(ValueError, match="maxRetries must be 0 or 1"):
        load_config(_write(tmp_path, BASE + f"maxRetries: {value}\n"))


@pytest.mark.parametrize("dataset", ["org/data", "org/data@latest", "org/data@main"])
def test_rejects_unpinned_dataset(tmp_path: Path, dataset: str) -> None:
    with pytest.raises(ValueError, match="dataset"):
        load_config(_write(tmp_path, BASE.replace("org/data@v1", dataset)))


def test_rejects_unknown_field(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown evaluation fields"):
        load_config(_write(tmp_path, BASE + "futureOption: true\n"))


def test_rejects_duplicate_tasks(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="task ids must be unique"):
        load_config(_write(tmp_path, BASE.replace("task-a, task-b", "task-a, task-a")))


def test_rejects_duplicate_candidates(tmp_path: Path) -> None:
    duplicate = (
        BASE
        + """\
  - id: first
    agent: oracle
    model: provider/model
"""
    )
    with pytest.raises(ValueError, match="candidate ids must be unique"):
        load_config(_write(tmp_path, duplicate))


def test_rejects_multiple_attempts_in_mvp(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="exactly one attempt"):
        load_config(_write(tmp_path, BASE.replace("attempts: 1", "attempts: 2")))


def test_rejects_eval_id_that_can_escape_work_directory(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="must contain only"):
        load_config(_write(tmp_path, BASE.replace("id: smoke", "id: ../outside")))


def test_checked_in_dataset_manifest_matches_evaluation_config() -> None:
    repository = Path(__file__).resolve().parents[1]
    config = load_config(repository / "evals" / "coding-smoke-v1.yaml")
    manifest = DatasetManifest.from_toml_file(
        repository / "evals" / "coding-smoke-v1.dataset.toml"
    )
    dataset_name, dataset_ref = config.dataset.rsplit("@", 1)

    assert manifest.dataset.name == dataset_name
    assert f"sha256:{manifest.compute_content_hash()}" == dataset_ref
    assert {task.name for task in manifest.tasks} == set(config.tasks)


def test_polyglot_30_config_is_balanced_and_haifa_only() -> None:
    repository = Path(__file__).resolve().parents[1]
    config = load_config(repository / "evals" / "coding-polyglot-30-v1.yaml")
    manifest = DatasetManifest.from_toml_file(
        repository / "evals" / "coding-polyglot-30-v1.dataset.toml"
    )
    languages = [task.split("_", 2)[1] for task in config.tasks]

    assert len(config.tasks) == 30
    assert {language: languages.count(language) for language in set(languages)} == {
        "cpp": 5,
        "go": 5,
        "java": 5,
        "javascript": 5,
        "python": 5,
        "rust": 5,
    }
    assert [candidate.id for candidate in config.candidates] == ["haifa"]
    assert config.dataset == (
        f"{manifest.dataset.name}@sha256:{manifest.compute_content_hash()}"
    )
    assert {task.name for task in manifest.tasks} == set(config.tasks)


def test_cpp_verifier_fixed_polyglot_30_config_matches_manifest() -> None:
    repository = Path(__file__).resolve().parents[1]
    config = load_config(
        repository / "evals" / "coding-polyglot-30-v1-cpp-verifier-fixed.yaml"
    )
    manifest = DatasetManifest.from_toml_file(
        repository
        / "evals"
        / "coding-polyglot-30-v1-cpp-verifier-fixed.dataset.toml"
    )

    assert len(config.tasks) == 30
    assert [candidate.id for candidate in config.candidates] == ["haifa"]
    assert config.dataset == (
        f"{manifest.dataset.name}@sha256:{manifest.compute_content_hash()}"
    )
    assert {task.name for task in manifest.tasks} == set(config.tasks)


def test_cpp_verifier_reward_fallback_patch_covers_selected_cpp_tasks() -> None:
    repository = Path(__file__).resolve().parents[1]
    patch = (
        repository
        / "evals"
        / "patches"
        / "aider-polyglot-selected-cpp-reward-fallback.patch"
    ).read_text(encoding="utf-8")

    assert patch.count("+trap write_default_reward EXIT") == 5
    for task in (
        "knapsack",
        "perfect-numbers",
        "binary-search-tree",
        "phone-number",
        "queen-attack",
    ):
        assert f"polyglot_cpp_{task}/tests/test.sh" in patch


def test_recovery_subset_contains_remaining_three_languages() -> None:
    repository = Path(__file__).resolve().parents[1]
    config = load_config(
        repository / "evals" / "coding-polyglot-15-java-python-rust-v1.yaml"
    )
    manifest = DatasetManifest.from_toml_file(
        repository
        / "evals"
        / "coding-polyglot-15-java-python-rust-v1.dataset.toml"
    )
    languages = [task.split("_", 2)[1] for task in config.tasks]

    assert len(config.tasks) == 15
    assert config.max_retries == 1
    assert {language: languages.count(language) for language in set(languages)} == {
        "java": 5,
        "python": 5,
        "rust": 5,
    }
    assert config.dataset == (
        f"{manifest.dataset.name}@sha256:{manifest.compute_content_hash()}"
    )
    assert {task.name for task in manifest.tasks} == set(config.tasks)
