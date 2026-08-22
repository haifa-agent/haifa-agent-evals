from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import time
from contextlib import suppress
from pathlib import Path


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


def default_state_path() -> Path:
    return _repository_root() / "work" / "infrastructure" / "proxy-relay.json"


def _vm_relay_script() -> Path:
    return _repository_root() / "infra" / "proxy" / "vm_proxy_relay.py"


def _port_open(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _run(command: list[str], *, timeout: int = 20) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _machine() -> dict:
    completed = _run(["podman", "machine", "inspect"])
    if completed.returncode != 0:
        raise RuntimeError("podman machine inspect failed")
    try:
        machines = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError("podman machine inspect returned invalid JSON") from error
    running = [machine for machine in machines if machine.get("State") == "running"]
    if len(running) != 1:
        raise RuntimeError("exactly one running Podman machine is required")
    return running[0]


def _ssh_identity(machine: dict) -> tuple[str, int, str]:
    ssh = machine.get("SSHConfig")
    if not isinstance(ssh, dict):
        raise RuntimeError("Podman machine SSH configuration is missing")
    identity = str(ssh.get("IdentityPath", ""))
    port = ssh.get("Port")
    user = str(ssh.get("RemoteUsername", ""))
    if not identity or not isinstance(port, int) or not user:
        raise RuntimeError("Podman machine SSH configuration is incomplete")
    return identity, port, user


def _vm_port_open(port: int) -> bool:
    command = (
        "python3 -c \"import socket; s=socket.create_connection(('127.0.0.1',"
        f"{port}),2); s.close()\""
    )
    return _run(["podman", "machine", "ssh", command]).returncode == 0


def _write_state(path: Path, payload: dict) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


def relay_status(
    *,
    source_host: str = "127.0.0.1",
    source_port: int = 2081,
    reverse_port: int = 1082,
    relay_port: int = 22081,
    state_path: Path | None = None,
) -> dict[str, object]:
    source_ready = _port_open(source_host, source_port)
    try:
        machine = _machine()
        reverse_ready = _vm_port_open(reverse_port)
        relay_ready = _vm_port_open(relay_port)
        machine_name = machine.get("Name")
    except RuntimeError:
        reverse_ready = False
        relay_ready = False
        machine_name = None
    state = None
    resolved_state = state_path or default_state_path()
    if resolved_state.is_file():
        try:
            state = json.loads(resolved_state.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            state = None
    ready = source_ready and reverse_ready and relay_ready
    return {
        "schemaVersion": 1,
        "status": "READY" if ready else "BLOCKED",
        "managed": bool(isinstance(state, dict) and state.get("managed")),
        "machine": machine_name,
        "source": {"host": source_host, "port": source_port, "reachable": source_ready},
        "vmReversePort": {"port": reverse_port, "reachable": reverse_ready},
        "vmRelayPort": {"port": relay_port, "reachable": relay_ready},
        "containerProxy": f"http://host.containers.internal:{relay_port}",
    }


def start_relay(
    *,
    source_host: str = "127.0.0.1",
    source_port: int = 2081,
    reverse_port: int = 1082,
    relay_port: int = 22081,
    state_path: Path | None = None,
) -> dict[str, object]:
    resolved_state = state_path or default_state_path()
    current = relay_status(
        source_host=source_host,
        source_port=source_port,
        reverse_port=reverse_port,
        relay_port=relay_port,
        state_path=resolved_state,
    )
    if current["status"] == "READY":
        if not resolved_state.is_file():
            _write_state(
                resolved_state,
                {
                    "schemaVersion": 1,
                    "managed": False,
                    "reason": "healthy relay already existed; no duplicate was started",
                },
            )
        return current
    if not current["source"]["reachable"]:  # type: ignore[index]
        raise RuntimeError("Windows proxy source is unreachable")
    if current["vmReversePort"]["reachable"] or current["vmRelayPort"]["reachable"]:  # type: ignore[index]
        raise RuntimeError("partial proxy relay already exists; refusing to start a duplicate")

    machine = _machine()
    identity, ssh_port, user = _ssh_identity(machine)
    ssh_command = [
        "ssh",
        "-i",
        identity,
        "-p",
        str(ssh_port),
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=15",
        "-o",
        "ServerAliveInterval=20",
        "-o",
        "ServerAliveCountMax=3",
        "-o",
        "ExitOnForwardFailure=yes",
        "-o",
        "StrictHostKeyChecking=no",
        "-N",
        "-R",
        f"{reverse_port}:{source_host}:{source_port}",
        f"{user}@127.0.0.1",
    ]
    tunnel = subprocess.Popen(  # noqa: S603
        ssh_command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not _vm_port_open(reverse_port):
        if tunnel.poll() is not None:
            raise RuntimeError("SSH reverse tunnel exited during startup")
        time.sleep(0.25)
    if not _vm_port_open(reverse_port):
        tunnel.terminate()
        raise RuntimeError("SSH reverse tunnel did not become reachable")

    script = _vm_relay_script()
    scp = _run(
        [
            "scp",
            "-i",
            identity,
            "-P",
            str(ssh_port),
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=no",
            str(script),
            f"{user}@127.0.0.1:/tmp/haifa-evals-vm-proxy-relay.py",
        ]
    )
    if scp.returncode != 0:
        tunnel.terminate()
        raise RuntimeError("failed to copy the VM proxy relay")
    remote_command = (
        "nohup python3 /tmp/haifa-evals-vm-proxy-relay.py "
        f"--listen-port {relay_port} --target-port {reverse_port} "
        ">/tmp/haifa-evals-vm-proxy-relay.log 2>&1 & echo $! "
        ">/tmp/haifa-evals-vm-proxy-relay.pid"
    )
    if _run(["podman", "machine", "ssh", remote_command]).returncode != 0:
        tunnel.terminate()
        raise RuntimeError("failed to start the VM proxy relay")
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not _vm_port_open(relay_port):
        time.sleep(0.25)
    if not _vm_port_open(relay_port):
        tunnel.terminate()
        raise RuntimeError("VM proxy relay did not become reachable")

    _write_state(
        resolved_state,
        {
            "schemaVersion": 1,
            "managed": True,
            "tunnelPid": tunnel.pid,
            "machine": machine.get("Name"),
            "sourceHost": source_host,
            "sourcePort": source_port,
            "reversePort": reverse_port,
            "relayPort": relay_port,
        },
    )
    return relay_status(
        source_host=source_host,
        source_port=source_port,
        reverse_port=reverse_port,
        relay_port=relay_port,
        state_path=resolved_state,
    )


def stop_relay(state_path: Path | None = None) -> dict[str, object]:
    resolved_state = state_path or default_state_path()
    if not resolved_state.is_file():
        return {"schemaVersion": 1, "status": "STOPPED", "detail": "no managed relay state"}
    state = json.loads(resolved_state.read_text(encoding="utf-8"))
    if not state.get("managed"):
        return {
            "schemaVersion": 1,
            "status": "UNCHANGED",
            "detail": "relay is externally managed and was not stopped",
        }
    _run(
        [
            "podman",
            "machine",
            "ssh",
            "if test -f /tmp/haifa-evals-vm-proxy-relay.pid; then "
            "kill $(cat /tmp/haifa-evals-vm-proxy-relay.pid) 2>/dev/null || true; "
            "rm -f /tmp/haifa-evals-vm-proxy-relay.pid; fi",
        ]
    )
    pid = state.get("tunnelPid")
    if isinstance(pid, int):
        with suppress(OSError):
            os.kill(pid, signal.SIGTERM)
    resolved_state.unlink(missing_ok=True)
    return {"schemaVersion": 1, "status": "STOPPED"}
