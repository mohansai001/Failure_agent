from dotenv import load_dotenv

load_dotenv()

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
