from ..Base_agent import Base_Agent
from utils.prompt_manager_v2 import AgentInstructionPrompt


class Failure_Agent(Base_Agent):
    name = "failure_agent"
    instructions = str(AgentInstructionPrompt("failure-agent-instructions"))
