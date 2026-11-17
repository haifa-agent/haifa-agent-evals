from pathlib import Path

from pier.agents.installed.base import BaseInstalledAgent, NonZeroAgentExitCodeError
from pier.models.agent.context import AgentContext
from pier.models.agent.network import NetworkAllowlist

from haifa_agent_evals.integrations.harbor.haifa_agent import HaifaAgentAdapter


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
