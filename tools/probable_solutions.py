from pathlib import Path
from vida.utils.llm import get_azure_response
from vida.utils.prompt_manager_v2 import (
    ToolDescriptionPrompt,
    ToolFieldsPrompt,
    GeneratorPrompt,
    AgentDescriptionPrompt,
)
from agent_framework import tool
from pydantic import Field
from typing import Annotated

_prob_solutions_field = ToolFieldsPrompt("probable-solutions-field-description")

def load_dependency_compatibility_skill() -> str:
    """Load the dependency/version compatibility skill used by the analyzer."""
    skill_path = Path(__file__).resolve().parent / "skill.md"

    if not skill_path.exists():
        raise FileNotFoundError(
            f"Dependency compatibility skill not found at: {skill_path}"
        )

    return skill_path.read_text(encoding="utf-8")


@tool(
    name="Probable_Solutions_Analyzer",
    description=str(
        ToolDescriptionPrompt("probable-solutions-description")
    ),
    approval_mode="never_require",
)
def ProbableSolutionsAnalyzer(
    prompt: Annotated[
        str,
        Field(description=_prob_solutions_field.get("prompt")),
    ]
) -> str:
    """Analyze a failure and return probable root causes and solutions."""
    print(
        "******************\n"
        "Probable Solutions Analyzer activated......\n"
        "********************"
    )

    knowledge_base = ""

    capabilities = (
        "Github Agent : \n"
        + str(AgentDescriptionPrompt("github-agent-description"))
        + "\n\n"
        + "YAML Agent : \n"
        + str(AgentDescriptionPrompt("yaml-agent-description"))
        + "\n\n"
        + "Terraform Agent : \n"
        + str(AgentDescriptionPrompt("tf-agent-description"))
        + "\n\n"
        + "ADO Agent : \n"
        + "Manages Azure DevOps projects, repositories, branches, commits, pull requests, "
          "pipelines, work items, and variable groups."
        + "\n\n"
        + "AzureDevOps Windows Self-Hosted Runner Manager : \n"
        + "Handles environment-level version compatibility issues on Azure DevOps self-hosted "
          "Windows runners by inspecting the runner, using its approved local package storage, "
          "installing the required version, and verifying the update. It does not modify application code."
        + "\n\n"
        + "ADO Rerun Failed Build : \n"
        + "Reruns a specific failed Azure DevOps build and returns the rerun result."
    )

    dependency_compatibility_skill = load_dependency_compatibility_skill()

    wrapper_prompt = GeneratorPrompt("probable-solutions-generator-prompt")
    final_prompt = wrapper_prompt.render(
        capabilities=capabilities,
        knowledge_base=knowledge_base,
        dependency_compatibility_skill=dependency_compatibility_skill,
        prompt=prompt,
    )

    response = get_azure_response(final_prompt)

    print(
        f"Response from Probable Solutions Analyzer:\n"
        f" ************************ {response}"
    )
    return response


