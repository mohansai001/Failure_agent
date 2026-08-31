from dotenv import load_dotenv
load_dotenv()
from vida.agents.Base_agent import Base_Agent
from vida.utils.prompt_manager_v2 import AgentInstructionPrompt
from tools.agent_calls import github_agent_tool_call, yaml_agent_tool_call, terraform_agent_tool_call, ado_agent_tool_call
from tools.probable_solutions import ProbableSolutionsAnalyzer
from vida.utils.config import Failure_agent_config as fail_config
from vida.utils.config import Base_agent_config as baconfig

class Failure_Agent(Base_Agent):
    name = "failure_agent"
    # model = fail_config.model
    # AI_endpoint = fail_config.AI_endpoint
    model = baconfig.model 
    AI_endpoint = baconfig.AI_endpoint
    instructions = str(AgentInstructionPrompt("failure-agent-instructions"))
    tools = [ github_agent_tool_call, yaml_agent_tool_call, terraform_agent_tool_call, ado_agent_tool_call, ProbableSolutionsAnalyzer]
