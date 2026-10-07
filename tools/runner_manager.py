from __future__ import annotations

from agent_framework import tool
from pydantic import Field
from typing import Annotated

from tools.azure_devops_windows_runner_adapter import AzureDevOpsWindowsSelfHostedRunnerAdapter


class RunnerManager:
    """Runner Manager for the currently supported Azure DevOps Windows runner."""

    def __init__(self) -> None:
        self.adapter = AzureDevOpsWindowsSelfHostedRunnerAdapter()

    def resolve_version_compatibility(
        self,
        runner_name: str,
        technology: str,
        required_version: str,
    ) -> dict:
        return self.adapter.resolve_version_compatibility(
            runner_name=runner_name,
            technology=technology,
            required_version=required_version,
        )


@tool(
    name="AzureDevOps_Windows_SelfHosted_Runner_Manager",
    description=(
        "Resolves environment-level version compatibility issues on an Azure DevOps "
        "self-hosted Windows runner. Identifies the runner through the supplied runner "
        "name, uses Azure VM Run Command against the configured Azure VM, checks the "
        "installed version, looks for the required package only in the configured local "
        "package storage, installs it when available, and verifies the result. It must "
        "never modify application source code. If the required package is not present "
        "in local storage, it stops and reports that to the caller."
    ),
    approval_mode="never_require",
)
def azure_devops_windows_self_hosted_runner_manager(
    runner_name: Annotated[str, Field(description="Azure DevOps self-hosted runner/agent name from the failed build timeline.")],
    technology: Annotated[str, Field(description="Dependency/runtime/tool technology, for example Python, .NET SDK, Node.js, npm, NuGet, Java, or Terraform.")],
    required_version: Annotated[str, Field(description="Required compatible version identified from the failure analysis.")],
) -> str:
    manager = RunnerManager()
    result = manager.resolve_version_compatibility(
        runner_name=runner_name,
        technology=technology,
        required_version=required_version,
    )
    import json
    return json.dumps(result, indent=2)