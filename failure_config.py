import os
from dotenv import load_dotenv
load_dotenv()

github_agent_url = os.getenv("Github_agent_URL")
yaml_agent_url = os.getenv("YAML_agent_URL")
terraform_agent_url = os.getenv("Terraform_agent_URL")