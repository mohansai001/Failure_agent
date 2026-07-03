from vida.utils.llm import get_azure_response
from vida.utils.prompt_manager_v2 import ToolDescriptionPrompt,ToolFieldsPrompt, GeneratorPrompt, AgentDescriptionPrompt
from agent_framework import tool
from pydantic import Field
from typing import Annotated    

_prob_solutions_field = ToolFieldsPrompt("probable-solutions-field-description")

@tool(name="Probable_Solutions_Analyzer", description=str(ToolDescriptionPrompt("probable-solutions-description")), approval_mode="never_require")
def ProbableSolutionsAnalyzer(prompt:Annotated[str,Field(description=_prob_solutions_field.get("prompt")) ]) -> str:
    """
    This tool analyzes the given prompt and provides probable solutions based on the context.
    """
    # Generate a response using the Azure LLM
    knowledge_base = ""
    capabilities = "Github Agent : \n" + str(AgentDescriptionPrompt("github-agent-description")) + "\n\n" + "YAML Agent : \n" + str(AgentDescriptionPrompt("yaml-agent-description")) + "\n\n" + "Terraform Agent : \n" + str(AgentDescriptionPrompt("tf-agent-description"))
    wrapper_prompt = GeneratorPrompt("probable-solutions-generator-prompt")
    final_prompt = wrapper_prompt.render(capabilities=capabilities, knowledge_base=knowledge_base, prompt=prompt)
    response = get_azure_response(final_prompt)
    
    # Return the generated response
    return response