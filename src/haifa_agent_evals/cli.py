from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from haifa_agent_evals.admission import admit
from haifa_agent_evals.collector import collect
from haifa_agent_evals.config import load_config
from haifa_agent_evals.dataset import configured_tasks_path
from haifa_agent_evals.doctor import doctor
from haifa_agent_evals.finalizer import finalize
from haifa_agent_evals.image_cache import (
    DEFAULT_IMAGE,
    build_image,
    inspect_image,
    prepare_task_images,
    seed_aider_runtime,
)
from haifa_agent_evals.infrastructure import (
    CONTAINER_PROXY_ENV,
    default_compose_overlay,
    run_compose_network_preflight,
)
from haifa_agent_evals.proxy_relay import relay_status, start_relay, stop_relay
from haifa_agent_evals.reporter import report
from haifa_agent_evals.runner import new_run_id, run


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="evals")
    commands = parser.add_subparsers(dest="command", required=True)

    run_parser = commands.add_parser("run", help="validate an eval config and run Harbor jobs")
    run_parser.add_argument("--config", type=Path, required=True)
    run_parser.add_argument("--work-dir", type=Path)
    run_parser.add_argument("--plan-only", action="store_true")
    run_parser.add_argument("--tasks-path", type=Path)
    run_parser.add_argument("--admission", type=Path)
    run_parser.add_argument("--doctor-output", type=Path)
    run_parser.add_argument("--jar", type=Path)
    run_parser.add_argument("--container-cli")
    run_parser.add_argument("--infra-evidence", type=Path)

    doctor_parser = commands.add_parser(
        "doctor", help="run read-only checks before a paid evaluation"
    )
    doctor_parser.add_argument("--config", type=Path, required=True)
    doctor_parser.add_argument("--tasks-path", type=Path)
    doctor_parser.add_argument("--admission", type=Path)
    doctor_parser.add_argument("--output", type=Path)
    doctor_parser.add_argument("--jar", type=Path)
    doctor_parser.add_argument("--container-cli")
    doctor_parser.add_argument("--infra-evidence", type=Path)

    admit_parser = commands.add_parser(
        "admit", help="validate a pinned dataset with oracle and nop calibration evidence"
    )
    admit_parser.add_argument("--config", type=Path, required=True)
    admit_parser.add_argument("--tasks-path", type=Path, required=True)
    admit_parser.add_argument("--oracle-job-dir", type=Path, required=True)
    admit_parser.add_argument("--nop-job-dir", type=Path, required=True)
    admit_parser.add_argument("--output", type=Path, required=True)

    collect_parser = commands.add_parser("collect", help="collect Harbor trials into CSV")
    collect_parser.add_argument("--job-dir", type=Path, required=True)
    collect_parser.add_argument("--output", type=Path, required=True)
    collect_parser.add_argument("--eval-id")
    collect_parser.add_argument("--config", type=Path)
    collect_parser.add_argument("--validate-evidence", action="store_true")

    report_parser = commands.add_parser("report", help="render a Markdown comparison")
    report_parser.add_argument("--results", type=Path, required=True)
    report_parser.add_argument("--output", type=Path)

    finalize_parser = commands.add_parser(
        "finalize", help="archive a complete Harbor run and generate verified reports"
    )
    finalize_parser.add_argument("--config", type=Path, required=True)
    finalize_parser.add_argument("--job-dir", type=Path, required=True)
    finalize_parser.add_argument("--archive-dir", type=Path, required=True)
    finalize_parser.add_argument("--run-manifest", type=Path)
    finalize_parser.add_argument("--admission", type=Path)
    finalize_parser.add_argument("--preflight", type=Path)

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

    infra_parser = commands.add_parser(
        "infra", help="manage and verify the evaluation network infrastructure"
    )
    infra_commands = infra_parser.add_subparsers(dest="infra_command", required=True)
    infra_check = infra_commands.add_parser(
        "check", help="run a no-model network probe in a real Harbor Compose task"
    )
    infra_check.add_argument("--output", type=Path, required=True)
    infra_check.add_argument("--proxy-url")
    infra_check.add_argument("--target-url", default="https://api.deepseek.com/")
    infra_check.add_argument("--overlay", type=Path, default=default_compose_overlay())
    infra_check.add_argument("--work-dir", type=Path)
    infra_check.add_argument("--container-cli", default="podman")

    proxy_parser = infra_commands.add_parser(
        "proxy", help="manage the Windows to Podman VM proxy relay"
    )
    proxy_commands = proxy_parser.add_subparsers(dest="proxy_command", required=True)
    for action in ("start", "status"):
        action_parser = proxy_commands.add_parser(action)
        action_parser.add_argument("--source-host", default="127.0.0.1")
        action_parser.add_argument("--source-port", type=int, default=2081)
        action_parser.add_argument("--reverse-port", type=int, default=1082)
        action_parser.add_argument("--relay-port", type=int, default=22081)
        action_parser.add_argument("--state", type=Path)
    proxy_stop = proxy_commands.add_parser("stop")
    proxy_stop.add_argument("--state", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "run":
        config = load_config(args.config)
        work_dir = args.work_dir or Path("work") / config.id / new_run_id()
        tasks_path = configured_tasks_path(config, args.tasks_path)
        admission = None
        doctor_output = None
        if not args.plan_only:
            admission = args.admission or Path("work") / "admissions" / f"{config.id}.json"
            doctor_output = (
                args.doctor_output or work_dir.parent / f"{work_dir.name}-preflight.json"
            )
            preflight = doctor(
                config,
                tasks_path,
                admission,
                doctor_output,
                jar_path=args.jar,
                container_cli=args.container_cli,
                infrastructure_evidence_path=args.infra_evidence,
            )
            if preflight["status"] != "READY":
                print(json.dumps(preflight, indent=2))
                return 2
        runner_tasks_path = tasks_path if not args.plan_only else args.tasks_path
        print(
            run(
                config,
                work_dir,
                args.plan_only,
                runner_tasks_path,
                args.jar,
                admission,
                doctor_output,
                args.infra_evidence,
                args.container_cli,
            )
        )
    elif args.command == "doctor":
        config = load_config(args.config)
        tasks_path = configured_tasks_path(config, args.tasks_path)
        admission = args.admission or Path("work") / "admissions" / f"{config.id}.json"
        output = args.output or Path("work") / "preflight" / f"{config.id}.json"
        preflight = doctor(
            config,
            tasks_path,
            admission,
            output,
            jar_path=args.jar,
            container_cli=args.container_cli,
            infrastructure_evidence_path=args.infra_evidence,
        )
        print(json.dumps(preflight, indent=2))
        if preflight["status"] != "READY":
            return 2
    elif args.command == "admit":
        config = load_config(args.config)
        admitted = admit(
            config,
            args.tasks_path,
            args.oracle_job_dir,
            args.nop_job_dir,
            args.output,
        )
        print(json.dumps({"status": admitted["status"], "output": str(args.output)}))
    elif args.command == "collect":
        config = load_config(args.config) if args.config else None
        results = collect(
            args.job_dir,
            args.output,
            args.eval_id,
            config,
            args.validate_evidence,
        )
        print(f"collected {len(results)} attempts into {args.output}")
    elif args.command == "report":
        print(report(args.results, args.output))
    elif args.command == "finalize":
        finalized = finalize(
            args.config,
            args.job_dir,
            args.archive_dir,
            run_manifest_path=args.run_manifest,
            admission_path=args.admission,
            preflight_path=args.preflight,
        )
        print(json.dumps(finalized, indent=2))
        if finalized["status"] != "COMPLETE":
            return 2
    elif args.command == "image" and args.image_command == "build":
        print(build_image(args.java_archive, args.image, args.container_cli, args.aider_runtime))
    elif args.command == "image" and args.image_command == "seed-aider":
        print(seed_aider_runtime(args.container, args.output, args.container_cli))
    elif args.command == "image" and args.image_command == "prepare-tasks":
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
    elif args.command == "image":
        print(json.dumps(inspect_image(args.image, args.container_cli), indent=2))
    elif args.infra_command == "check":
        proxy_url = args.proxy_url or os.environ.get(CONTAINER_PROXY_ENV)
        if not proxy_url:
            raise ValueError(
                f"--proxy-url or {CONTAINER_PROXY_ENV} is required for infrastructure check"
            )
        evidence = run_compose_network_preflight(
            args.output,
            proxy_url=proxy_url,
            target_url=args.target_url,
            overlay=args.overlay,
            work_dir=args.work_dir,
            container_cli=args.container_cli,
        )
        print(json.dumps(evidence, indent=2))
        if evidence["status"] != "READY":
            return 2
    elif args.proxy_command == "stop":
        print(json.dumps(stop_relay(args.state), indent=2))
    else:
        operation = start_relay if args.proxy_command == "start" else relay_status
        result = operation(
            source_host=args.source_host,
            source_port=args.source_port,
            reverse_port=args.reverse_port,
            relay_port=args.relay_port,
            state_path=args.state,
        )
        print(json.dumps(result, indent=2))
        if result["status"] != "READY":
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
