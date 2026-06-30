from dotenv import load_dotenv
load_dotenv()
from vida.agents.Base_agent import Base_Agent
from vida.utils.prompt_manager_v2 import AgentInstructionPrompt
from tools.agent_calls import github_agent_tool_call, yaml_agent_tool_call, terraform_agent_tool_call


class Failure_Agent(Base_Agent):
    name = "failure_agent"
    instructions = str(AgentInstructionPrompt("failure-agent-instructions"))
    tools = [github_agent_tool_call, yaml_agent_tool_call, terraform_agent_tool_call]
