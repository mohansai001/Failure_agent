import requests
from agent_framework import tool
from typing import Annotated
from pydantic import Field
from vida.utils.prompt_manager_v2 import AgentDescriptionPrompt, ToolFieldsPrompt
from vida.utils.github_client import get_github_client
from vida.utils.request_context import github_pat_ctx, task_id_ctx
from failure_config import yaml_agent_url, terraform_agent_url, github_agent_url, ado_agent_url
import json
from vida.utils.logger import get_logger
logger = get_logger(__name__)


_git_agent_field = ToolFieldsPrompt("git-agent-field-description")
@tool(name="Github_Agent", description=str(AgentDescriptionPrompt("github-agent-description")), approval_mode="never_require")
def github_agent_tool_call(prompt: Annotated[str, Field(description = _git_agent_field.get("prompt"))]) -> str:
    logger.info("[Github_Agent] called by [Failure Agent]")
    pat_token = github_pat_ctx.get(None)
    task_id = task_id_ctx.get(None)
    url = github_agent_url
    if url:
        response = requests.post(url, json={"prompt": prompt, "pat_token": pat_token, "task_id": task_id})
        json_response = json.loads(response.text)
        final_response = json_response["output"]
        return final_response
    return "No URL configured for Github agent."

_yaml_agent_field = ToolFieldsPrompt("yaml-agent-field-description")
@tool(name="Yaml_Agent", description=str(AgentDescriptionPrompt("yaml-agent-description")), approval_mode="never_require")
def yaml_agent_tool_call(prompt: Annotated[str, Field(description = _yaml_agent_field.get("prompt"))]) -> str:
    logger.info("[Yaml_Agent] called by [Failure Agent]")
    task_id = task_id_ctx.get(None)
    url = yaml_agent_url
    if url:
        response = requests.post(url, json={"prompt": prompt, "task_id": task_id})
        json_response = json.loads(response.text)
        final_response = json_response["output"]
        return final_response
    return "No URL configured for Yaml agent."

_terraform_agent_field = ToolFieldsPrompt("tf-agent-field-description")
@tool(name="Terraform_Agent", description=str(AgentDescriptionPrompt("tf-agent-description")), approval_mode="never_require")
def terraform_agent_tool_call(prompt: Annotated[str, Field(description = _terraform_agent_field.get("prompt"))]) -> str:
    logger.info("[Terraform_Agent] called by [Failure Agent]")
    task_id = task_id_ctx.get(None)
    url = terraform_agent_url
    if url:
        response = requests.post(url, json={"prompt": prompt, "task_id": task_id})
        json_response = json.loads(response.text)
        final_response = json_response["output"]
        return final_response
    return "No URL configured for Terraform agent."

@tool(name="ADO_Agent", description="Delegates Azure DevOps tasks to the ADO Agent. Use this agent to manage ADO projects, repositories, branches, commits, pull requests, pipelines, work items, and variable groups. Call this when any task involves Azure DevOps operations.", approval_mode="never_require")
def ado_agent_tool_call(prompt: Annotated[str, Field(description="Full instructions for the ADO Agent describing the Azure DevOps task to perform. Include all relevant details such as project name, repository name, branch, pipeline name, or any other context needed to complete the task.")]) -> str:
    logger.info("[ADO_Agent] called by [Failure Agent]")
    task_id = task_id_ctx.get(None)
    url = ado_agent_url
    if url:
        response = requests.post(url, json={"prompt": prompt, "task_id": task_id})
        json_response = json.loads(response.text)
        final_response = json_response["output"]
        return final_response
    return "No URL configured for ADO agent."
