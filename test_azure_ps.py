import os
from dotenv import load_dotenv
from azure.identity import DefaultAzureCredential
from azure.mgmt.compute import ComputeManagementClient

load_dotenv()

subscription_id = os.environ["AZURE_SUBSCRIPTION_ID"]
resource_group = os.environ["AZURE_VM_RESOURCE_GROUP"]
vm_name = os.environ["AZURE_VM_NAME"]

credential = DefaultAzureCredential(exclude_cli_credential=True)

compute_client = ComputeManagementClient(
    credential=credential,
    subscription_id=subscription_id,
)

script = [
    r"""
    Write-Output "Computer: $env:COMPUTERNAME"
    Write-Output "Package directory:"
    Write-Output "C:\Users\Application_Packages"

    Write-Output "Files:"
    Get-ChildItem -LiteralPath "C:\Users\Application_Packages" -Force |
        Select-Object Name, Extension, Length
    """
]

result = compute_client.virtual_machines.begin_run_command(
    resource_group_name=resource_group,
    vm_name=vm_name,
    parameters={
        "commandId": "RunPowerShellScript",
        "script": script,
    },
).result()

for item in result.value:
    print(f"{item.code}: {item.message}")