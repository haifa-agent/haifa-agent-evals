from __future__ import annotations

import argparse
import json
from pathlib import Path

from haifa_agent_evals.collector import collect
from haifa_agent_evals.config import load_config
from haifa_agent_evals.image_cache import (
    DEFAULT_IMAGE,
    build_image,
    inspect_image,
    prepare_task_images,
    seed_aider_runtime,
)
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

    image_parser = commands.add_parser("image", help="manage the agent infrastructure image")
    image_commands = image_parser.add_subparsers(dest="image_command", required=True)
    image_build = image_commands.add_parser("build", help="build and smoke-test the image")
    image_build.add_argument("--java-archive", type=Path)
    image_build.add_argument("--image", default=DEFAULT_IMAGE)
    image_build.add_argument("--container-cli")
    image_build.add_argument("--aider-runtime", type=Path)
    image_seed = image_commands.add_parser(
        "seed-aider", help="copy only the pinned Aider runtime from a stopped trial"
    )
    image_seed.add_argument("--container", required=True)
    image_seed.add_argument("--output", type=Path)
    image_seed.add_argument("--container-cli")
    image_prepare = image_commands.add_parser(
        "prepare-tasks", help="build a new digest-pinned task environment set"
    )
    image_prepare.add_argument("--config", type=Path, required=True)
    image_prepare.add_argument("--tasks-path", type=Path, required=True)
    image_prepare.add_argument("--output", type=Path)
    image_prepare.add_argument("--image", default=DEFAULT_IMAGE)
    image_prepare.add_argument("--container-cli")
    image_check = image_commands.add_parser("check", help="inspect and smoke-test the image")
    image_check.add_argument("--image", default=DEFAULT_IMAGE)
    image_check.add_argument("--container-cli")
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
    elif args.command == "report":
        print(report(args.results, args.output))
    elif args.image_command == "build":
        print(build_image(args.java_archive, args.image, args.container_cli, args.aider_runtime))
    elif args.image_command == "seed-aider":
        print(seed_aider_runtime(args.container, args.output, args.container_cli))
    elif args.image_command == "prepare-tasks":
        print(
            json.dumps(
                prepare_task_images(
                    args.config,
                    args.tasks_path,
                    args.output,
                    args.image,
                    args.container_cli,
                ),
                indent=2,
            )
        )
    else:
        print(json.dumps(inspect_image(args.image, args.container_cli), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
