from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path
from typing import Any

import yaml
from harbor.models.dataset.manifest import DatasetInfo, DatasetManifest, DatasetTaskRef
from harbor.publisher.packager import Packager

from haifa_agent_evals.config import EvaluationConfig, load_config

DEFAULT_IMAGE = "localhost/haifa-agent-evals/agent-infra:jammy-jdk21-aider0.86.2-v1"
JDK_SHA256 = "f2dc5418092c43003db8f9005c4a286e1c0104fea96ccdd49e8ebd037cac9219"
_AIDER_TOOL_PATH = Path("root/.local/share/uv/tools/aider-chat")
_AIDER_PYTHON_PATH = Path("root/.local/share/uv/python/cpython-3.12.8-linux-x86_64-gnu")


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _container_cli(configured: str | None = None) -> str:
    if configured:
        resolved = shutil.which(configured)
        if resolved:
            return resolved
        raise ValueError(f"container CLI is not available: {configured}")
    for candidate in ("podman", "docker"):
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    raise ValueError("Podman or Docker is required to manage the infrastructure image")


def _java_archive(configured: Path | None) -> Path:
    value = configured or (
        Path(os.environ["HAIFA_EVAL_JAVA_ARCHIVE_PATH"])
        if os.environ.get("HAIFA_EVAL_JAVA_ARCHIVE_PATH")
        else None
    )
    if value is None or not value.is_file():
        raise ValueError(
            "a readable pinned JDK archive is required via --java-archive or "
            "HAIFA_EVAL_JAVA_ARCHIVE_PATH"
        )
    resolved = value.expanduser().resolve()
    if _sha256(resolved) != JDK_SHA256:
        raise ValueError("JDK archive digest does not match Temurin 21.0.8+9")
    return resolved


def _default_aider_runtime() -> Path:
    return (
        _repository_root()
        / "work"
        / "image-cache"
        / "agent-infra"
        / "aider-runtime.tar.gz"
    )


def _validate_aider_runtime(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise ValueError(
            "Aider runtime is incomplete; run 'evals image seed-aider --container <id>'"
        )
    with tarfile.open(resolved, "r:gz") as archive:
        names = {member.name.rstrip("/") for member in archive.getmembers()}
    required = {
        (_AIDER_TOOL_PATH / "bin" / "aider").as_posix(),
        (_AIDER_TOOL_PATH / "bin" / "python").as_posix(),
        (_AIDER_PYTHON_PATH / "bin" / "python3.12").as_posix(),
    }
    if not required <= names:
        raise ValueError(
            "Aider runtime is incomplete; run 'evals image seed-aider --container <id>'"
        )
    forbidden_names = {"aider.chat.history.md", "aider.txt", "haifa-trace.jsonl"}
    if any(Path(name).name in forbidden_names for name in names):
        raise ValueError("Aider runtime contains evaluation output")
    allowed_prefixes = {
        _AIDER_TOOL_PATH.as_posix(),
        _AIDER_PYTHON_PATH.as_posix(),
    }
    if any(not any(name.startswith(prefix) for prefix in allowed_prefixes) for name in names):
        raise ValueError("Aider runtime contains paths outside the pinned runtime")
    return resolved


def seed_aider_runtime(
    container: str,
    output: Path | None = None,
    container_cli: str | None = None,
) -> Path:
    if not container.strip():
        raise ValueError("source container must be non-empty")
    cli = _container_cli(container_cli)
    target = (output or _default_aider_runtime()).expanduser().resolve()
    if target.exists():
        return _validate_aider_runtime(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    running = subprocess.run(  # noqa: S603
        [cli, "inspect", "--format", "{{.State.Running}}", container],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip().lower() == "true"
    if not running:
        subprocess.run([cli, "start", container], check=True, capture_output=True)  # noqa: S603
    temporary = target.with_name(f"{target.name}.partial")
    try:
        with temporary.open("wb") as stream:
            subprocess.run(  # noqa: S603
                [
                    cli,
                    "exec",
                    container,
                    "tar",
                    "-C",
                    "/",
                    "-czf",
                    "-",
                    _AIDER_TOOL_PATH.as_posix(),
                    _AIDER_PYTHON_PATH.as_posix(),
                ],
                check=True,
                stdout=stream,
                stderr=subprocess.PIPE,
            )
        temporary.replace(target)
    finally:
        if not running:
            subprocess.run(  # noqa: S603
                [cli, "stop", "--time", "3", container],
                check=True,
                capture_output=True,
            )
    return _validate_aider_runtime(target)


def _inspect(cli: str, image: str) -> dict[str, Any]:
    completed = subprocess.run(  # noqa: S603
        [cli, "image", "inspect", image],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    raw = json.loads(completed.stdout)
    if not isinstance(raw, list) or len(raw) != 1 or not isinstance(raw[0], dict):
        raise ValueError("container CLI returned an unexpected image inspection payload")
    return raw[0]


def _lock_payload(image: str, inspected: dict[str, Any]) -> dict[str, Any]:
    config = inspected.get("Config") if isinstance(inspected.get("Config"), dict) else {}
    labels = config.get("Labels") if isinstance(config.get("Labels"), dict) else {}
    return {
        "schemaVersion": 1,
        "image": image,
        "id": inspected.get("Id"),
        "digest": inspected.get("Digest"),
        "repoDigests": inspected.get("RepoDigests") or [],
        "sizeBytes": inspected.get("Size"),
        "architecture": inspected.get("Architecture"),
        "os": inspected.get("Os"),
        "labels": labels,
        "inputs": {
            "aiderVersion": "0.86.2",
            "javaVersion": "21.0.8+9",
            "jdkArchiveSha256": JDK_SHA256,
        },
    }


def _pinned_reference(image: str, inspected: dict[str, Any]) -> str:
    repo_digests = inspected.get("RepoDigests") or []
    repository = image.rsplit(":", 1)[0]
    matching = [
        reference
        for reference in repo_digests
        if str(reference).startswith(f"{repository}@")
    ]
    if matching:
        return str(matching[0])
    if repo_digests:
        return str(repo_digests[0])
    digest = inspected.get("Digest")
    if digest:
        return f"{repository}@{digest}"
    raise ValueError("built image has no content digest")


def _agent_ready_dockerfile(source_reference: str, infra_reference: str) -> str:
    return (
        f"FROM {infra_reference} AS haifa_agent_infra\n\n"
        f"FROM {source_reference}\n\n"
        "COPY --from=haifa_agent_infra /opt/haifa/java /opt/haifa/java\n"
        "COPY --from=haifa_agent_infra /opt/haifa/agent-infra.properties "
        "/opt/haifa/agent-infra.properties\n"
        "COPY --from=haifa_agent_infra /root/.local/share/uv/tools/aider-chat "
        "/root/.local/share/uv/tools/aider-chat\n"
        "COPY --from=haifa_agent_infra "
        "/root/.local/share/uv/python/cpython-3.12.8-linux-x86_64-gnu "
        "/root/.local/share/uv/python/cpython-3.12.8-linux-x86_64-gnu\n"
        "RUN install -d -m 0755 /root/.local/bin && "
        "ln -sf /root/.local/share/uv/tools/aider-chat/bin/aider "
        "/root/.local/bin/aider && "
        "printf '%s\\n' 'export PATH=\"$HOME/.local/bin:$PATH\"' "
        "> /root/.local/bin/env && chmod 0644 /root/.local/bin/env\n"
        "ENV JAVA_HOME=/opt/haifa/java\n"
        "ENV PATH=/opt/haifa/java/bin:/root/.local/bin:${PATH}\n"
        'LABEL io.haifa.evals.agent-infra="jammy-jdk21-aider0.86.2-v1"\n'
    )


def _host_tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(candidate for candidate in root.rglob("*") if candidate.is_file()):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(path.read_bytes())
    return digest.hexdigest()


_CONTAINER_TREE_HASH_SCRIPT = """
import hashlib
from pathlib import Path
root = Path('/app')
digest = hashlib.sha256()
for path in sorted(candidate for candidate in root.rglob('*') if candidate.is_file()):
    relative = path.relative_to(root).as_posix().encode('utf-8')
    digest.update(len(relative).to_bytes(4, 'big'))
    digest.update(relative)
    digest.update(path.read_bytes())
print(digest.hexdigest())
""".strip()


def _task_image_signature(dockerfile: str) -> str:
    signatures = (
        "libboost-all-dev",
        "go1.21.5.linux",
        "openjdk-21-jdk",
        "deb.nodesource.com/setup_20.x",
        "deadsnakes/ppa",
        "sh.rustup.rs",
    )
    found = [signature for signature in signatures if signature in dockerfile]
    if len(found) != 1:
        raise ValueError("task Dockerfile has no unique language image signature")
    return found[0]


def _cached_task_image(
    cli: str,
    source_task: Path,
    inventory: list[dict[str, Any]],
) -> str:
    slug = source_task.name
    expected_hash = _host_tree_hash(source_task / "environment" / "workspace")
    tagged = [
        image
        for image in inventory
        if any(slug in str(tag) for tag in image.get("RepoTags") or [])
    ]
    dockerfile = (source_task / "environment" / "Dockerfile").read_text(encoding="utf-8")
    signature = _task_image_signature(dockerfile)
    candidates = tagged or [
        image
        for image in inventory
        if image.get("Config", {}).get("WorkingDir") == "/app"
        and signature
        in "\n".join(str(entry.get("created_by", "")) for entry in image.get("History", []))
        and any(
            "COPY dir:" in str(entry.get("created_by", ""))
            for entry in image.get("History", [])
        )
    ]
    for image in candidates:
        image_id = str(image["Id"])
        completed = subprocess.run(  # noqa: S603
            [
                cli,
                "run",
                "--rm",
                "--entrypoint",
                "python3",
                image_id,
                "-c",
                _CONTAINER_TREE_HASH_SCRIPT,
            ],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        if completed.stdout.strip() == expected_hash:
            return image_id
    raise ValueError(f"no cached Harbor task image matches workspace: {slug}")


def _image_inventory(cli: str) -> list[dict[str, Any]]:
    ids = sorted(
        set(
            subprocess.run(  # noqa: S603
                [cli, "images", "-aq", "--no-trunc"],
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
            ).stdout.splitlines()
        )
    )
    if not ids:
        return []
    completed = subprocess.run(  # noqa: S603
        [cli, "image", "inspect", *ids],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    payload = json.loads(completed.stdout)
    return [entry for entry in payload if isinstance(entry, dict)]


def _task_with_prebuilt_image(task_toml: str, image_reference: str) -> str:
    if re.search(r"(?m)^docker_image\s*=", task_toml):
        raise ValueError("task already declares docker_image")
    marker = "[environment]"
    if marker not in task_toml:
        raise ValueError("task.toml has no [environment] section")
    return task_toml.replace(marker, f'{marker}\ndocker_image = "{image_reference}"', 1)


def _generated_evaluation_config(
    source: EvaluationConfig,
    generated_id: str,
    dataset: str,
) -> dict[str, Any]:
    return {
        "id": generated_id,
        "dataset": dataset,
        "tasks": list(source.tasks),
        "attempts": source.attempts,
        "timeoutMinutes": source.timeout_minutes,
        "candidates": [
            {"id": candidate.id, "agent": candidate.agent, "model": candidate.model}
            for candidate in source.candidates
        ],
    }


def prepare_task_images(
    config_path: Path,
    tasks_path: Path,
    output: Path | None = None,
    infra_image: str = DEFAULT_IMAGE,
    container_cli: str | None = None,
) -> dict[str, Any]:
    from haifa_agent_evals.runner import _validate_local_dataset

    config = load_config(config_path)
    source_tasks = tasks_path.expanduser().resolve()
    source_manifest = _repository_root() / "evals" / f"{config.id}.dataset.toml"
    if not source_manifest.is_file():
        raise ValueError("source evaluation has no pinned local dataset manifest")
    _validate_local_dataset(config, source_tasks, source_manifest)

    cli = _container_cli(container_cli)
    infra_inspected = _inspect(cli, infra_image)
    infra_reference = _pinned_reference(infra_image, infra_inspected)
    generated_id = f"{config.id}-agent-infra-v1"
    destination = (
        output
        or _repository_root()
        / "work"
        / "image-cache"
        / "task-environments"
        / generated_id
    ).expanduser().resolve()
    if destination.exists():
        config_output = destination / f"{generated_id}.yaml"
        manifest_output = destination / f"{generated_id}.dataset.toml"
        tasks_output = destination / "tasks"
        if config_output.is_file() and manifest_output.is_file() and tasks_output.is_dir():
            return {
                "config": str(config_output),
                "datasetManifest": str(manifest_output),
                "tasksPath": str(tasks_output),
                "infraImage": infra_reference,
                "reused": True,
            }
        raise ValueError("task image output exists but is incomplete")

    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f"{generated_id}-", dir=destination.parent))
    generated_tasks = staging / "tasks"
    generated_tasks.mkdir()
    inventory = _image_inventory(cli)
    task_refs: list[DatasetTaskRef] = []
    images: list[dict[str, str]] = []
    for task_name in config.tasks:
        slug = task_name.rsplit("/", 1)[-1]
        source_task = source_tasks / slug
        generated_task = generated_tasks / slug
        shutil.copytree(source_task, generated_task)
        dockerfile = generated_task / "environment" / "Dockerfile"
        source_digest = Packager.compute_content_hash(source_task)[0]
        safe_slug = re.sub(r"[^a-z0-9_.-]+", "-", slug.lower())
        source_image_id = _cached_task_image(cli, source_task, inventory)
        source_image_tag = (
            f"localhost/haifa-agent-evals/source-task-{safe_slug}:{source_digest[:16]}"
        )
        subprocess.run(  # noqa: S603
            [cli, "tag", source_image_id, source_image_tag],
            check=True,
        )
        source_reference = _pinned_reference(source_image_tag, _inspect(cli, source_image_tag))
        dockerfile.write_text(
            _agent_ready_dockerfile(source_reference, infra_reference),
            encoding="utf-8",
        )
        identity = hashlib.sha256(f"{source_digest}:{infra_reference}".encode()).hexdigest()[:16]
        image_tag = f"localhost/haifa-agent-evals/task-{safe_slug}:{identity}"
        subprocess.run(  # noqa: S603
            [
                cli,
                "build",
                "--pull=never",
                "--tag",
                image_tag,
                "--file",
                str(dockerfile),
                str(dockerfile.parent),
            ],
            check=True,
        )
        image_inspected = _inspect(cli, image_tag)
        image_reference = _pinned_reference(image_tag, image_inspected)
        task_toml = generated_task / "task.toml"
        task_toml.write_text(
            _task_with_prebuilt_image(task_toml.read_text(encoding="utf-8"), image_reference),
            encoding="utf-8",
        )
        task_digest = f"sha256:{Packager.compute_content_hash(generated_task)[0]}"
        task_refs.append(DatasetTaskRef(name=task_name, digest=task_digest))
        images.append({"task": task_name, "image": image_reference, "digest": task_digest})

    source_dataset_name = config.dataset.rsplit("@", 1)[0]
    dataset_name = f"{source_dataset_name}-agent-infra-v1"
    manifest = DatasetManifest(
        dataset=DatasetInfo(
            name=dataset_name,
            description="Pinned coding smoke tasks with the Haifa/Aider infrastructure image",
        ),
        tasks=task_refs,
    )
    manifest_path = staging / f"{generated_id}.dataset.toml"
    manifest_path.write_text(manifest.to_toml(), encoding="utf-8")
    dataset = f"{dataset_name}@sha256:{manifest.compute_content_hash()}"
    generated_config_path = staging / f"{generated_id}.yaml"
    generated_config_path.write_text(
        yaml.safe_dump(
            _generated_evaluation_config(config, generated_id, dataset),
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    summary = {
        "config": str(destination / generated_config_path.name),
        "datasetManifest": str(destination / manifest_path.name),
        "tasksPath": str(destination / "tasks"),
        "infraImage": infra_reference,
        "dataset": dataset,
        "images": images,
        "reused": False,
    }
    (staging / "images.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    staging.rename(destination)
    return summary


def build_image(
    java_archive: Path | None = None,
    image: str = DEFAULT_IMAGE,
    container_cli: str | None = None,
    aider_runtime: Path | None = None,
) -> Path:
    cli = _container_cli(container_cli)
    archive = _java_archive(java_archive)
    runtime = _validate_aider_runtime(aider_runtime or _default_aider_runtime())
    repository = _repository_root()
    source = repository / "infra" / "agent-base"
    context = repository / "work" / "image-cache" / "agent-infra" / "context"
    context.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source / "Dockerfile", context / "Dockerfile")
    shutil.copy2(source / ".containerignore", context / ".containerignore")
    shutil.copy2(source / "aider-requirements.lock", context / "aider-requirements.lock")
    shutil.copy2(source / "verify_aider_lock.py", context / "verify_aider_lock.py")
    context_runtime = context / "aider-runtime.tar.gz"
    if not context_runtime.is_file() or context_runtime.stat().st_size != runtime.stat().st_size:
        shutil.copy2(runtime, context_runtime)
    cached_archive = context / "temurin-jdk.tar.gz"
    if not cached_archive.is_file() or _sha256(cached_archive) != JDK_SHA256:
        shutil.copy2(archive, cached_archive)

    subprocess.run(  # noqa: S603
        [
            cli,
            "build",
            "--pull=never",
            "--tag",
            image,
            "--file",
            str(context / "Dockerfile"),
            str(context),
        ],
        check=True,
    )
    inspect_image(image, container_cli=cli, run_smoke=True)
    lock_path = context.parent / "image-lock.json"
    lock_path.write_text(
        json.dumps(_lock_payload(image, _inspect(cli, image)), indent=2) + "\n",
        encoding="utf-8",
    )
    return lock_path


def inspect_image(
    image: str = DEFAULT_IMAGE,
    container_cli: str | None = None,
    run_smoke: bool = True,
) -> dict[str, Any]:
    cli = _container_cli(container_cli)
    inspected = _inspect(cli, image)
    if run_smoke:
        smoke_command = (
            "set -eu; "
            "test -r /opt/haifa/agent-infra.properties; "
            "test ! -e /opt/haifa/haifa-agent.jar; "
            "test ! -e /app/instruction.md; "
            "java -version 2>&1 | grep -q '\"21'; "
            "java --list-modules | grep -q '^jdk.random@'; "
            "test \"$(aider --version)\" = 'aider 0.86.2'; "
            "printf 'agent infrastructure image smoke: PASS\\n'"
        )
        subprocess.run(  # noqa: S603
            [cli, "run", "--rm", image, "sh", "-lc", smoke_command],
            check=True,
        )
    return _lock_payload(image, inspected)
