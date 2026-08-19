from __future__ import annotations

import argparse
from pathlib import Path

from haifa_agent_evals.collector import collect
from haifa_agent_evals.config import load_config
from haifa_agent_evals.reporter import report
from haifa_agent_evals.runner import run


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="evals")
    commands = parser.add_subparsers(dest="command", required=True)

    run_parser = commands.add_parser("run", help="validate an eval config and run Harbor jobs")
    run_parser.add_argument("--config", type=Path, required=True)
    run_parser.add_argument("--work-dir", type=Path)
    run_parser.add_argument("--plan-only", action="store_true")

    collect_parser = commands.add_parser("collect", help="collect Harbor trials into CSV")
    collect_parser.add_argument("--job-dir", type=Path, required=True)
    collect_parser.add_argument("--output", type=Path, required=True)
    collect_parser.add_argument("--eval-id")

    report_parser = commands.add_parser("report", help="render a Markdown comparison")
    report_parser.add_argument("--results", type=Path, required=True)
    report_parser.add_argument("--output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "run":
        config = load_config(args.config)
        work_dir = args.work_dir or Path("work") / config.id
        print(run(config, work_dir, args.plan_only))
    elif args.command == "collect":
        results = collect(args.job_dir, args.output, args.eval_id)
        print(f"collected {len(results)} attempts into {args.output}")
    else:
        print(report(args.results, args.output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
