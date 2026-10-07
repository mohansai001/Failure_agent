"""Direct smoke tests for the Azure DevOps Windows self-hosted runner adapter."""

from tools.azure_devops_windows_runner_adapter import (
    AzureDevOpsWindowsSelfHostedRunnerAdapter,
)


def run_test(adapter: AzureDevOpsWindowsSelfHostedRunnerAdapter, label: str, script: str) -> None:
    print(f"\n--- {label} ---")
    result = adapter.execute_powershell(script)
    print(f"success   : {result.get('success')}")
    print(f"status    : {result.get('status_code')}")
    print(f"stdout    : {result.get('stdout', '')}")
    print(f"stderr    : {result.get('stderr', '')}")

    if not result.get("success"):
        raise RuntimeError(f"{label} failed: {result}")


if __name__ == "__main__":
    adapter = AzureDevOpsWindowsSelfHostedRunnerAdapter()

    # Test 1: prove PowerShell is executing on the target VM.
    run_test(adapter, "Computer name", "$env:COMPUTERNAME")

    # Test 2: prove a real dependency/runtime version command executes remotely.
    run_test(adapter, "Python version", "python --version")

    print("\nAzure DevOps Windows Runner Adapter smoke tests passed.")
