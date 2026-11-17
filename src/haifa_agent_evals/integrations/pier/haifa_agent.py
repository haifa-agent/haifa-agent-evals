import shlex
from pathlib import Path

from pier.agents.installed.base import BaseInstalledAgent, NonZeroAgentExitCodeError
from pier.environments.base import BaseEnvironment, ExecResult
from pier.models.agent.context import AgentContext
from pier.models.agent.network import NetworkAllowlist

from haifa_agent_evals.integrations.harbor.haifa_agent import HaifaAgentAdapter

_PROXY_SCRIPT = "/opt/haifa/opencode_proxy.py"


class HaifaCodingAgent(HaifaAgentAdapter, BaseInstalledAgent):
    """Use Haifa in Pier's isolated DeepSWE agent environment."""

    nonzero_exit_error = NonZeroAgentExitCodeError

    def __init__(self, *args: object, timeout_seconds: int = 2700, **kwargs: object) -> None:
        config = Path(__file__).parents[1] / "harbor" / "haifa-eval-opencode-go.yaml"
        super().__init__(
            *args,
            config_path=config,
            opencode_go_auth=True,
            timeout_seconds=timeout_seconds,
            **kwargs,
        )

    def network_allowlist(self) -> NetworkAllowlist:
        return NetworkAllowlist(domains=["opencode.ai"])

    def install_spec(self) -> None:
        # Install the host's pinned artifacts during trial setup.
        return None

    def populate_context_post_run(self, context: AgentContext) -> None:
        # The shared adapter captures the exit status and archives the trajectory.
        return None

    async def install(self, environment: BaseEnvironment) -> None:
        await super().install(environment)
        await environment.upload_file(Path(__file__).with_name("opencode_proxy.py"), _PROXY_SCRIPT)

    async def _execute_cli(self, environment: BaseEnvironment, command: str) -> ExecResult:
        # Pier assigns each trial an authenticated, domain-restricted egress proxy. The JVM
        # uses a local CONNECT adapter, while the task's verifier stays independently offline.
        proxy_command = (
            f"python3 {_PROXY_SCRIPT} & "
            "PROXY_PID=$!; trap 'kill \"$PROXY_PID\" 2>/dev/null || true' EXIT; "
            "sleep 1; kill -0 \"$PROXY_PID\" || exit 1; "
            "unset HTTPS_PROXY HTTP_PROXY ALL_PROXY https_proxy http_proxy all_proxy; "
            "export JAVA_TOOL_OPTIONS='-Dhttps.proxyHost=127.0.0.1 -Dhttps.proxyPort=8317'; "
            f"sh -c {shlex.quote(command)}"
        )
        return await environment.exec(
            command=proxy_command,
            env=environment.agent_process_env(None),
            timeout_sec=self.timeout_seconds,
            user="root",
        )
