from dotenv import load_dotenv
load_dotenv()

import winrm
from vida.utils.AzureSecrets import get_azure_secret_value
from failure_config import (
    runner_ip_secret_name,
    runner_username_secret_name,
    runner_password_secret_name,
    winrm_port,
    winrm_scheme,
    winrm_transport,
    winrm_server_cert_validation,
)

ip = get_azure_secret_value(runner_ip_secret_name)
username = get_azure_secret_value(runner_username_secret_name)
password = get_azure_secret_value(runner_password_secret_name)

session = winrm.Session(
    f"{winrm_scheme}://{ip}:{winrm_port}/wsman",
    auth=(username, password),
    transport=winrm_transport,
    server_cert_validation=winrm_server_cert_validation,
)

result = session.run_ps("$env:COMPUTERNAME")
print(f"stdout   : {result.std_out.decode().strip()}")
print(f"stderr   : {result.std_err.decode().strip()}")
print(f"exit code: {result.status_code}")

if result.status_code == 0:
    print("WinRM connection successful.")
else:
    print("WinRM connection failed.")