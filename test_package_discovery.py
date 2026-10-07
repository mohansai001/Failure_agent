from __future__ import annotations

import json

from tools.azure_devops_windows_runner_adapter import (
    AzureDevOpsWindowsSelfHostedRunnerAdapter,
)


def main() -> None:
    adapter = AzureDevOpsWindowsSelfHostedRunnerAdapter()

    print("--- Local installer discovery ---")
    files = adapter._list_package_files()

    if not files:
        print("No .exe/.msi installers were found in the configured runner package storage.")
        raise SystemExit(1)

    for filename in files:
        print(filename)

    print("\n--- Match Python 3.13.6 ---")
    commands = adapter._ask_llm_for_commands(
        technology="python",
        required_version="3.13.6",
        available_files=files,
    )

    print(json.dumps(commands, indent=2))

    matched_file = commands.get("matched_file")
    if not matched_file:
        print("\nFAIL: No installer was matched for Python 3.13.6.")
        raise SystemExit(1)

    print(f"\nMatched installer: {matched_file}")
    print("Package discovery test passed. No installation was performed.")


if __name__ == "__main__":
    main()
