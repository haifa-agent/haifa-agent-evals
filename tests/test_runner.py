from pathlib import Path

from haifa_agent_evals.config import Candidate, EvaluationConfig
from haifa_agent_evals.runner import build_commands, run


def test_builds_one_harbor_command_per_candidate(tmp_path: Path) -> None:
    config = EvaluationConfig(
        id="smoke",
        dataset="org/data@v1",
        tasks=("task-a", "task-b"),
        attempts=1,
        timeout_minutes=20,
        candidates=(
            Candidate("haifa", "package:Haifa", "provider/model"),
            Candidate("aider", "aider", "provider/model"),
        ),
    )
    commands = build_commands(config, tmp_path)
    assert len(commands) == 2
    assert commands[0][:4] == ["harbor", "run", "--dataset", "org/data@v1"]
    assert commands[0].count("--include-task-name") == 2

    plan = run(config, tmp_path, plan_only=True)
    assert plan.is_file()
    assert not any(path.name == "result.json" for path in tmp_path.rglob("result.json"))
