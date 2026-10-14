import inspect
import logging
import os
import re
from typing import Any

from dotenv import load_dotenv

load_dotenv()

# Azure App Service Log Stream will capture INFO-level application logs.
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("failure_agent")
logger.setLevel(logging.INFO)

# Avoid writing credentials or long token-like values to logs.
_SECRET_PATTERN = re.compile(
    r"(?i)\b(api[-_ ]?key|subscription[-_ ]?key|client[-_ ]?secret|secret|access[-_ ]?token|"
    r"token|password|passwd|pwd|connection[-_ ]?string|authorization|sas)\b"
    r"(\s*[:=]\s*)(\"[^\"]*\"|'[^']*'|\S+)"
)
_LONG_TOKEN_PATTERN = re.compile(r"\b[A-Za-z0-9_-]{48,}\b")


def _safe_log_value(value: Any, limit: int = 3000) -> str:
    """Render a log-safe, length-limited representation of an agent result."""
    try:
        text = value if isinstance(value, str) else repr(value)
    except Exception:
        text = f"<{type(value).__name__}>"
    text = _SECRET_PATTERN.sub(r"\1\2***REDACTED***", text)
    text = _LONG_TOKEN_PATTERN.sub("***REDACTED_LONG_VALUE***", text)
    if len(text) > limit:
        text = text[:limit] + "... [truncated]"
    return text


logger.info("event=agent.module_loaded log_level=INFO")

from vida.agents.Base_agent import Base_Agent
from vida.utils.prompt_manager_v2 import AgentInstructionPrompt
from tools.agent_calls import (
    github_agent_tool_call,
    yaml_agent_tool_call,
    terraform_agent_tool_call,
    ado_agent_tool_call,
)
from tools.probable_solutions import ProbableSolutionsAnalyzer
from tools.runner_manager import azure_devops_windows_self_hosted_runner_manager
from tools.ado_pipeline_tools import ado_rerun_failed_build
from vida.utils.config import Failure_agent_config as fail_config
from vida.utils.config import Base_agent_config as baconfig


VERSION_COMPATIBILITY_ROUTING = """

ADDITIONAL FAILURE HANDLING: AZURE DEVOPS SELF-HOSTED WINDOWS VERSION COMPATIBILITY

When an environment-level version compatibility/ version mismatch issue is identified:

1. Use the given diagnosis.
2. Do not modify application source code, package.json, .csproj files, lock files, or other development dependency declarations.
3. If the diagnosis states that the issue is a version compatibility issue and the fix is environment-level, call AzureDevOps_Windows_SelfHosted_Runner_Manager.
4. Pass the runner name, technology, and required compatible version identified by the analyzer.
5. Wait for the Runner Manager result.
6. If the Runner Manager reports that the required package/version is not available in the configured local package storage, stop the remediation flow and report that to the user. Do not download anything from the internet.
7. If the Runner Manager successfully updates and verifies the runner, call ADO_Rerun_Failed_Build using the project and original build ID from the failure context.
8. A rerun means retrying the failed Azure DevOps build. Do not queue a new pipeline run.
9. If the rerun succeeds, report the successful remediation.
10. If the rerun fails again, pass the new failure information back through Probable Solutions Analyzer for another diagnosis.
11. If a code/project dependency issue is identified, do not do anything. Report the issue to the user and suggest that they fix the code/project dependency issue in their source code and retry the build.
12. Do not invent runner names, versions, capabilities, credentials, or package locations.

LOGGING EXPECTATIONS:
- Produce concise, user-safe progress updates at major workflow transitions: diagnosis started/completed, routing decision, runner remediation started/completed, failed-build retry started/completed, and final outcome.
- Do not reveal hidden chain-of-thought, credentials, access tokens, or full environment settings in progress updates.
- Detailed deterministic logs are emitted by the Analyzer, Runner Manager, and runner adapter.
"""


class Failure_Agent(Base_Agent):
    name = "failure_agent"
    # model = fail_config.model
    # AI_endpoint = fail_config.AI_endpoint
    model = baconfig.model
    AI_endpoint = baconfig.AI_endpoint
    instructions = str(AgentInstructionPrompt("failure-agent-instructions")) + VERSION_COMPATIBILITY_ROUTING
    tools = [
        github_agent_tool_call,
        yaml_agent_tool_call,
        terraform_agent_tool_call,
        ado_agent_tool_call,
        ProbableSolutionsAnalyzer,
        azure_devops_windows_self_hosted_runner_manager,
        ado_rerun_failed_build,
    ]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        logger.info("event=agent.initializing agent=%s", self.name)
        super().__init__(*args, **kwargs)
        logger.info("event=agent.initialized agent=%s tool_count=%d", self.name, len(self.tools))

    def run(self, *args: Any, **kwargs: Any) -> Any:
        """Log the top-level run lifecycle while preserving sync/async Base_Agent APIs."""
        logger.info(
            "event=agent.run.started agent=%s arg_types=%s kwarg_names=%s",
            self.name,
            [type(arg).__name__ for arg in args],
            sorted(kwargs.keys()),
        )
        try:
            result = super().run(*args, **kwargs)
        except Exception:
            logger.exception("event=agent.run.failed agent=%s stage=invoke", self.name)
            raise

        if inspect.isawaitable(result):
            return self._log_awaitable_result(result)
        if inspect.isasyncgen(result):
            return self._log_async_generator_result(result)
        if inspect.isgenerator(result):
            return self._log_generator_result(result)

        logger.info("event=agent.run.completed agent=%s final_output=%s", self.name, _safe_log_value(result))
        return result

    async def _log_awaitable_result(self, awaitable: Any) -> Any:
        try:
            result = await awaitable
        except Exception:
            logger.exception("event=agent.run.failed agent=%s stage=await", self.name)
            raise
        logger.info("event=agent.run.completed agent=%s final_output=%s", self.name, _safe_log_value(result))
        return result

    async def _log_async_generator_result(self, generator: Any):
        chunks = []
        try:
            async for chunk in generator:
                if len(chunks) < 20:
                    chunks.append(_safe_log_value(chunk, 300))
                yield chunk
        except Exception:
            logger.exception("event=agent.run.failed agent=%s stage=stream", self.name)
            raise
        finally:
            logger.info(
                "event=agent.run.stream_completed agent=%s chunk_count_captured=%d output_preview=%s",
                self.name,
                len(chunks),
                _safe_log_value(" | ".join(chunks), 2000),
            )

    def _log_generator_result(self, generator: Any):
        chunks = []
        try:
            for chunk in generator:
                if len(chunks) < 20:
                    chunks.append(_safe_log_value(chunk, 300))
                yield chunk
        except Exception:
            logger.exception("event=agent.run.failed agent=%s stage=stream", self.name)
            raise
        finally:
            logger.info(
                "event=agent.run.stream_completed agent=%s chunk_count_captured=%d output_preview=%s",
                self.name,
                len(chunks),
                _safe_log_value(" | ".join(chunks), 2000),
            )
