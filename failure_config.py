import os
from dotenv import load_dotenv

load_dotenv()

github_agent_url = os.getenv("Github_agent_URL")
yaml_agent_url = os.getenv("YAML_agent_URL")
terraform_agent_url = os.getenv("Terraform_agent_URL")
ado_agent_url = os.getenv("ADO_agent_URL")

# Azure Key Vault 
Azure_Secrets_URL = os.getenv("Azure_Secrets_URL")
azure_secrets_url = Azure_Secrets_URL

# Azure VM target used by Azure Run Command
AZURE_SUBSCRIPTION_ID = os.getenv("AZURE_SUBSCRIPTION_ID")
AZURE_VM_RESOURCE_GROUP = "chatpdf"
AZURE_VM_NAME = "chatpdf123"

azure_subscription_id = AZURE_SUBSCRIPTION_ID
azure_vm_resource_group = AZURE_VM_RESOURCE_GROUP
azure_vm_name = AZURE_VM_NAME

# Windows self-hosted runner package storage path on the VM.
runner_package_root = os.getenv("RUNNER_PACKAGE_ROOT", r"C:\Users\Application_Packages")

# WinRM config
winrm_port = int(os.getenv("WINRM_PORT", "5985"))
winrm_scheme = os.getenv("WINRM_SCHEME", "https").lower()
winrm_transport = os.getenv("WINRM_TRANSPORT", "ntlm").lower()
winrm_server_cert_validation = os.getenv("WINRM_SERVER_CERT_VALIDATION", "validate").lower()

# Second runner
runner_ip_secret_name = os.getenv("RUNNER_IP_SECRET_NAME", "chatpdf-IP")
runner_username_secret_name = os.getenv("RUNNER_USERNAME_SECRET_NAME", "chatpdf-username")
runner_password_secret_name = os.getenv("RUNNER_PASSWORD_SECRET_NAME", "chatpdf-Password")

# Azure DevOps API config reused for rerun operations.
ADO_ORG_URL = os.getenv("ADO_ORG_URL")
ADO_PAT = os.getenv("ADO_PAT")
ado_org_url = ADO_ORG_URL
ado_pat = ADO_PAT
