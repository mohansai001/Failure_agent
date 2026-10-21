"""
Azure DevOps Windows self-hosted runner adapter.

Flow (resolve_version_compatibility):
    1. Discover installers (.exe/.msi) on the target VM.
    2. LLM proposes inspect / install / verify commands.
    3. Code qualifies the installer path and validates every command.
       The LLM is never trusted as an execution authority.
    4. Run inspect (informational) -> install -> verify.
    5. Verify the required version from an executable located under the fixed
       installation destination, then run code-owned post-install smoke tests.
    6. On installer failure, collect generic Windows diagnostics.

Installation policy:
    All version-compatibility installations performed by the Failure Agent
    MUST target:
        C:\\Users\\Agent_Installations

Execution policy:
    All commands run through Azure VM Run Command, one at a time
    (process lock + retry on HTTP 409).

Security policy:
    LLM-generated commands are deterministically validated before execution.
    Only a locally discovered installer under runner_package_root may be
    executed. Output is redacted before it is returned/logged.
"""

from __future__ import annotations

import json
import re
import threading
import time
from pathlib import PureWindowsPath
from typing import Any

from azure.core.exceptions import HttpResponseError
from azure.identity import DefaultAzureCredential
from azure.mgmt.compute import ComputeManagementClient

from failure_config import (
    AZURE_SUBSCRIPTION_ID,
    AZURE_VM_NAME,
    AZURE_VM_RESOURCE_GROUP,
    runner_package_root,
)
from vida.utils.llm import get_azure_response


class UnsafeRunnerCommand(ValueError):
    """Raised when an LLM-generated PowerShell command violates the safety policy."""


# =============================================================================
# Fixed installation destination
# =============================================================================

# Determined destination for every Failure Agent version-compatibility install.
_INSTALL_DESTINATION = r"C:\Users\Agent_Installations"


# =============================================================================
# Safety policy
# =============================================================================

_MAX_COMMAND_LENGTH = 8000

_BLOCKED_SYNTAX = re.compile(
    "|".join(
        (
            r"`",
            r"\$\(",
            r"\$\{",
            r"\|",
            r">",
            r"&&",
            r"\|\|",
            r"-EncodedCommand\b",
            r"\[System\.IO\.File\]::(?:Write|Append|Create|Delete|Move|Copy)",
            r"\[System\.Diagnostics\.Process\]::Start",
        )
    ),
    re.IGNORECASE,
)

_BLOCKED_COMMANDS = re.compile(
    r"\b(?:"
    + "|".join(
        (
            # downloads / remote execution
            r"Invoke-WebRequest",
            r"Invoke-RestMethod",
            r"Start-BitsTransfer",
            r"DownloadString",
            r"DownloadFile",
            r"WebClient",
            r"curl(?:\.exe)?",
            r"wget(?:\.exe)?",
            r"certutil(?:\.exe)?",
            r"bitsadmin(?:\.exe)?",
            # nested shells / script hosts
            r"Invoke-Expression",
            r"Invoke-Command",
            r"powershell(?:\.exe)?",
            r"pwsh(?:\.exe)?",
            r"cmd(?:\.exe)?",
            r"wscript(?:\.exe)?",
            r"cscript(?:\.exe)?",
            r"mshta(?:\.exe)?",
            r"rundll32(?:\.exe)?",
            r"regsvr32(?:\.exe)?",
            # system configuration
            r"reg(?:\.exe)?",
            r"sc(?:\.exe)?",
            r"schtasks(?:\.exe)?",
            r"net(?:\.exe)?",
            r"taskkill(?:\.exe)?",
            r"Set-ExecutionPolicy",
            r"(?:Add|Set)-MpPreference",
            r"Format-Volume",
            r"Clear-Disk",
            r"Initialize-Disk",
            r"(?:New|Remove|Resize)-Partition",
            r"(?:Set|New|Remove|Start|Stop|Restart)-Service",
            r"(?:Register|Unregister|Start|Stop)-ScheduledTask",
            r"(?:Set|New|Remove)-NetFirewallRule",
            # file modification
            r"(?:Remove|New)-Item(?:Property)?",
            r"(?:Set|Add)-Content",
            r"Out-File",
            r"(?:Copy|Move|Rename)-Item",
        )
    )
    + r")\b",
    re.IGNORECASE,
)

_SIMPLE_COMMAND = re.compile(r"^[A-Za-z0-9_.-]+(?:\.exe)?$")
_VERSION_SELECTOR = re.compile(r"^-\d+(?:\.\d+)*$")
_VERSION_FLAGS = {
    "--version",
    "-version",
    "-v",
    "--help",
    "-help",
    "-h",
    "help",
    "version",
    "-about",
}

_QUOTED = r"'[^']*'|\"[^\"]*\""
_PARAM_VALUES = rf"(?:\s+(?:{_QUOTED})(?:\s*,\s*(?:{_QUOTED}))*)?"
_INSTALL_SHAPE = re.compile(
    r"^\s*(?:\$p\s*=\s*)?Start-Process\b(?P<body>.*?)"
    r"(?:;\s*if\s*\(\s*\$p\.ExitCode\s*-ne\s*0\s*\)\s*\{\s*exit\s+\$p\.ExitCode\s*\})?\s*$",
    re.IGNORECASE,
)
_INSTALL_BODY = re.compile(rf"(?:\s*-[A-Za-z]+{_PARAM_VALUES})+\s*")
_INSTALL_PARAM = re.compile(rf"-([A-Za-z]+)({_PARAM_VALUES})")
_ALLOWED_PARAMS = {"filepath", "argumentlist", "wait", "passthru", "nonewwindow"}
_SAFE_VALUE = {
    "'": re.compile(r"[A-Za-z0-9_ ./:=\\\"()\-]*"),
    '"': re.compile(r"[A-Za-z0-9_ ./:=\\()\-]*"),
}

_REBOOT_CODES = {1641, 3010}

# Technology-specific required install arguments are data-driven.
# TargetDir is included so the destination is enforced deterministically.
_REQUIRED_INSTALL_ARGS: dict[str, tuple[str, ...]] = {
    "python": (
        "InstallAllUsers=1",
        "PrependPath=1",
        "TargetDir=C:\\Users\\Agent_Installations",
        "Include_lib=1",
        "Include_exe=1",
        "Include_launcher=1",
        "Include_pip=1",
        "Include_tcltk=1",
    ),
}

# Code-owned smoke tests: the LLM never writes these commands.
_SMOKE_TESTS: dict[str, list[tuple[str, str]]] = {
    "python": [
        ("stdlib", "-c 'import encodings, ssl, sqlite3, zlib, ctypes'"),
        ("pip", "-m pip --version"),
    ],
    "node": [
        ("run", "-e 'process.exit(0)'"),
        ("npm", "--version"),
    ],
    "java": [
        ("javac", "-version"),
    ],
}

# Installed executable names allowed for deterministic inspect/verify validation.
# This is an execution safety allowlist, not a per-technology installation workflow.
_RUNTIME_EXECUTABLES: dict[str, tuple[str, ...]] = {
    "python": ("python", "python.exe", "python3", "python3.exe", "py", "py.exe"),
    "node": ("node", "node.exe", "npm", "npm.cmd"),
    "java": ("java", "java.exe", "javac", "javac.exe"),
}


# =============================================================================
# Secret redaction
# =============================================================================

_SECRET_PATTERNS = (
    (
        re.compile(
            r"(?i)\b(api[-_ ]?key|subscription[-_ ]?key|client[-_ ]?secret|secret|access[-_ ]?token|"
            r"token|password|passwd|pwd|account[-_ ]?key|connection[-_ ]?string|authorization|sas)\b"
            r"(\s*[:=]\s*)(\"[^\"]*\"|'[^']*'|\S+)"
        ),
        r"\1\2***REDACTED***",
    ),
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}"), "Bearer ***REDACTED***"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}\b"), "***REDACTED_JWT***"),
    (re.compile(r"(?i)([?&](?:sig|sv|se|key|token|code)=)[^&\s\"']+"), r"\1***REDACTED***"),
    (re.compile(r"\b[A-Za-z0-9]{32,}\b"), "***REDACTED***"),
)

_RAW_OUTPUT_PHASES = {"package_discovery", "installer_failure_diagnostics"}


# =============================================================================
# PowerShell templates (adapter-owned)
# =============================================================================

_WRAPPER = r"""
$ErrorActionPreference = 'Stop'
$machinePath = [Environment]::GetEnvironmentVariable('Path', 'Machine')
$userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
if ($machinePath -or $userPath) { $env:Path = @($machinePath, $userPath) -join ';' }

try {
@@SCRIPT@@
    $exitCode = if ($null -eq $LASTEXITCODE) { 0 } else { [int]$LASTEXITCODE }
}
catch {
    Write-Error $_
    $exitCode = 1
}

Write-Output '__RUNNER_PHASE__=@@PHASE@@'
Write-Output "__RUNNER_EXIT_CODE__=$exitCode"
""".strip()

_DISCOVERY_SCRIPT = r"""
$root = '@@ROOT@@'
if (-not (Test-Path -LiteralPath $root -PathType Container)) { throw "Package directory not found: $root" }
Get-ChildItem -LiteralPath $root -File |
    Where-Object { $_.Extension -in '.exe', '.msi' } |
    Sort-Object Name |
    ForEach-Object { $_.Name }
""".strip()

_DIAGNOSTICS_SCRIPT = r"""
$installer = '@@PATH@@'
$cutoff = (Get-Date).AddMinutes(-15)
$r = [ordered]@{
    installer_path = $installer
    installer_name = '@@NAME@@'
    installer_exit_code = @@EXIT@@
    installer_exists = $false
    installer_size_bytes = $null
    installer_signature_status = $null
    free_space_gb = $null
    windows_installer_service_status = 'Unavailable'
    pending_reboot = $false
    msiexec_process_count = 0
    recent_installer_logs = @()
    recent_windows_events = @()
}

try {
    $item = Get-Item -LiteralPath $installer -ErrorAction Stop
    $r.installer_exists = $true
    $r.installer_size_bytes = [int64]$item.Length
    $r.installer_signature_status = (Get-AuthenticodeSignature -FilePath $installer).Status.ToString()
} catch {}
try { $r.free_space_gb = [math]::Round((Get-PSDrive -Name $installer.Substring(0, 1)).Free / 1GB, 2) } catch {}
try { $r.windows_installer_service_status = (Get-Service -Name msiserver).Status.ToString() } catch {}
try { $r.msiexec_process_count = @(Get-Process -Name msiexec -ErrorAction SilentlyContinue).Count } catch {}

try {
    $rebootKeys = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending',
                  'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired'
    $r.pending_reboot = [bool]($rebootKeys | Where-Object { Test-Path -LiteralPath $_ })
    if (-not $r.pending_reboot) {
        $s = Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager' -Name PendingFileRenameOperations -ErrorAction SilentlyContinue
        $r.pending_reboot = [bool]$s.PendingFileRenameOperations
    }
} catch {}

try {
    $r.recent_installer_logs = @(foreach ($dir in ($env:TEMP, 'C:\Windows\Temp' | Select-Object -Unique)) {
        Get-ChildItem -LiteralPath $dir -Filter *.log -File -ErrorAction SilentlyContinue |
            Where-Object { $_.LastWriteTime -gt $cutoff } |
            Sort-Object LastWriteTime -Descending | Select-Object -First 3 |
            ForEach-Object {
                $lines = Get-Content -LiteralPath $_.FullName -Tail 400 -ErrorAction SilentlyContinue |
                    Where-Object { $_ -match '(?i)error|fail|1603|0x[0-9a-f]{8}|rollback|newer|already' } |
                    Select-Object -Last 15
                [ordered]@{ file = $_.Name; key_lines = @($lines) }
            }
    })
} catch {}

try {
    $r.recent_windows_events = @(foreach ($log in 'Application', 'System') {
        Get-WinEvent -FilterHashtable @{ LogName = $log; StartTime = $cutoff; Level = 2 } -ErrorAction SilentlyContinue |
            Where-Object {
                $_.ProviderName -match '(?i)MsiInstaller|Application Error|Windows Error Reporting' -or
                $_.Message -match [regex]::Escape($r.installer_name) -or
                $_.Message -match '(?i)1603|fatal error'
            } |
            Select-Object -First 8 |
            ForEach-Object {
                $m = [string]$_.Message
                [ordered]@{
                    log_name = $log; time = $_.TimeCreated.ToString('o'); id = $_.Id
                    provider = $_.ProviderName; message = $m.Substring(0, [math]::Min(600, $m.Length))
                }
            }
    })
} catch {}

$r | ConvertTo-Json -Depth 5 -Compress
""".strip()


# =============================================================================
# LLM prompt
# =============================================================================

_LLM_RULES = (
    "Return JSON only (no markdown) with exactly these fields: matched_file, inspect_command, install_command, verify_command.",
    "matched_file must be copied exactly from available_local_installer_files. If none is suitable, return an empty matched_file.",
    "Every command is ONE line.",
    "No URLs, downloads, Internet access, network paths or UNC paths.",
    "Never launch another shell/interpreter: powershell.exe, pwsh.exe, cmd.exe, wscript.exe, cscript.exe, mshta.exe, rundll32.exe, regsvr32.exe.",
    "Never use Invoke-Expression or Invoke-Command.",
    "Never delete/create/copy/move/rename files, or change the registry, services, scheduled tasks, firewall, users, disks or execution policy.",
    "No command chaining, pipes, redirection, backticks, subexpressions, variables or encoded commands.",
    "inspect_command checks the CURRENTLY INSTALLED runtime before installation. It must use a bare runtime command name and a version/help selector, such as python --version.",
    "inspect_command must NEVER execute or reference matched_file or package_root.",
    "verify_command checks the NEWLY INSTALLED runtime AFTER installation. It MUST execute the runtime executable from the fixed installation destination.",
    f"verify_command MUST reference an executable path located under exactly '{_INSTALL_DESTINATION}'.",
    "verify_command must not reference the installer file.",
    "For verify_command, use '<fixed-destination-executable> <version flag>'. If the executable path contains spaces, use the PowerShell call operator '&' with the quoted path; otherwise the call operator is optional.",
    "install_command must execute ONLY matched_file using Start-Process.",
    f"install_command MUST explicitly target exactly '{_INSTALL_DESTINATION}' using the installer's native destination/target argument.",
    "Install silently and machine-wide where supported.",
    "Every -ArgumentList value must be a quoted string.",
    "Do not rely on installer defaults for core runtime components.",
    "The installer file is used ONLY by install_command.",
)

_LLM_EXAMPLE = (
    "Example for matched_file = python-3.13.2-amd64.exe:\n"
    "  inspect_command = python --version\n"
    "  install_command = Start-Process -FilePath '<package_root>\\python-3.13.2-amd64.exe' "
    "-ArgumentList '/quiet','InstallAllUsers=1','PrependPath=1',"
    "'TargetDir=C:\\Users\\Agent_Installations','Include_lib=1',"
    "'Include_exe=1','Include_launcher=1','Include_pip=1','Include_tcltk=1' -Wait -PassThru\n"
    "  verify_command = C:\\Users\\Agent_Installations\\python.exe --version"
)


class AzureDevOpsWindowsSelfHostedRunnerAdapter:
    """
    Remediates environment-version problems on a Windows self-hosted runner VM.

    Installation destination is deterministic and fixed at:
        C:\\Users\\Agent_Installations
    """

    _run_command_lock = threading.Lock()
    _RETRY_DELAYS = (10, 20, 30, 60, 60, 60, 60)

    def __init__(self) -> None:
        self.subscription_id = AZURE_SUBSCRIPTION_ID
        self.resource_group = AZURE_VM_RESOURCE_GROUP
        self.vm_name = AZURE_VM_NAME
        self.package_root = str(runner_package_root).strip().rstrip("\\/")
        self._client: ComputeManagementClient | None = None

    # -------------------------------------------------------------------------
    # Helpers: redaction and paths
    # -------------------------------------------------------------------------

    @staticmethod
    def _redact(value: Any) -> str:
        text = str(value)
        for pattern, replacement in _SECRET_PATTERNS:
            text = pattern.sub(replacement, text)
        return text

    @classmethod
    def _redact_deep(cls, value: Any) -> Any:
        if isinstance(value, str):
            return cls._redact(value)
        if isinstance(value, list):
            return [cls._redact_deep(item) for item in value]
        if isinstance(value, dict):
            return {key: cls._redact_deep(item) for key, item in value.items()}
        return value

    @staticmethod
    def _path_casefold(path: str) -> str:
        return PureWindowsPath(path).as_posix().rstrip("/").casefold()

    @classmethod
    def _path_is_under(cls, path: str, root: str) -> bool:
        candidate = cls._path_casefold(path)
        base = cls._path_casefold(root)
        return candidate == base or candidate.startswith(base + "/")

    @staticmethod
    def _normalize_install_argument(value: str) -> str:
        """
        Normalize an installer argument for safe semantic comparison.

        The LLM/JSON representation may contain escaped Windows separators,
        while the deterministic required-argument policy contains normal
        Windows separators. They are semantically equivalent for this check.

        This normalization is used only for argument comparison. It does not
        relax the separate remote/network-path safety checks.
        """
        value = str(value).strip().strip("'\"")
        value = value.replace("/", "\\")
        value = re.sub(r"\\+", r"\\", value)
        return value.casefold()

    def _installer_path(self, matched_file: str) -> str:
        return str(PureWindowsPath(self.package_root) / matched_file)

    def _qualify_installer_path(self, command: str, matched_file: str) -> str:
        """Qualify a bare local installer filename to its approved package-root path."""
        full = self._installer_path(matched_file)
        return re.sub(
            rf"(?<![\\/\w.-]){re.escape(matched_file)}",
            lambda _m: full,
            command,
            flags=re.IGNORECASE,
        )

    @staticmethod
    def _command_executable(command: str) -> tuple[str, bool]:
        """
        Extract the first executable token and whether the command uses PowerShell '&'.

        Supported forms:
            python --version
            C:\\Users\\Agent_Installations\\python.exe --version
            & 'C:\\Users\\Agent_Installations\\python.exe' --version
        """
        match = re.match(
            r"^\s*(?:(?P<call>&)\s+)?(?:'(?P<single>[^']+)'|\"(?P<double>[^\"]+)\"|(?P<bare>[^\s]+))",
            command,
        )
        if not match:
            return "", False
        executable = match.group("single") or match.group("double") or match.group("bare") or ""
        return executable.strip(), bool(match.group("call"))

    # -------------------------------------------------------------------------
    # Command validation
    # -------------------------------------------------------------------------

    def _base_safety_check(self, command: str) -> None:
        if not isinstance(command, str) or not command.strip():
            raise UnsafeRunnerCommand("Empty PowerShell command.")
        if len(command) > _MAX_COMMAND_LENGTH:
            raise UnsafeRunnerCommand("Command exceeds the maximum allowed length.")
        if any(ch in command for ch in ("\x00", "\r", "\n")):
            raise UnsafeRunnerCommand("Command must be a single line.")

        found = _BLOCKED_SYNTAX.search(command)
        if found:
            raise UnsafeRunnerCommand(f"Blocked PowerShell syntax: {found.group(0)}")

        found = _BLOCKED_COMMANDS.search(command)
        if found:
            raise UnsafeRunnerCommand(f"Blocked PowerShell command: {found.group(0)}")

        if re.search(r"(^|\s)\.\s+[^. ]", command):
            raise UnsafeRunnerCommand("Dot-sourcing is not allowed.")

    def _validate_inspect_or_verify(
        self,
        command: str,
        *,
        require_fixed_destination: bool,
        technology: str,
        matched_file: str,
    ) -> None:
        """Validate a read-only runtime version command."""
        self._base_safety_check(command)

        executable, uses_call_operator = self._command_executable(command)
        if not executable:
            raise UnsafeRunnerCommand("Could not identify the inspect/verify executable.")

        # Arguments begin after the executable. Strip a leading '&' + executable
        # without allowing arbitrary PowerShell expressions.
        remainder = re.sub(
            r"^\s*(?:&\s+)?(?:'[^']+'|\"[^\"]+\"|[^\s]+)",
            "",
            command,
            count=1,
        ).strip()
        parts = remainder.split() if remainder else []
        if not 1 <= len(parts) <= 3:
            raise UnsafeRunnerCommand("Inspect/verify must contain a version/help selector.")

        for arg in parts:
            clean = arg.strip("'\"").lower()
            if clean in _VERSION_FLAGS:
                continue
            if _VERSION_SELECTOR.fullmatch(clean):
                continue
            raise UnsafeRunnerCommand("Inspect/verify arguments are restricted to version/help selectors.")

        tech_key = technology.strip().lower()
        allowed_names = {name.casefold() for name in _RUNTIME_EXECUTABLES.get(tech_key, ())}

        if require_fixed_destination:
            # Verification must execute the installed runtime from the fixed destination,
            # never whichever version happens to win PATH resolution.
            if not self._path_is_under(executable, _INSTALL_DESTINATION):
                raise UnsafeRunnerCommand(
                    f"Verify command must execute an installed executable under {_INSTALL_DESTINATION}."
                )
            if executable.casefold().endswith(".msi") or executable.casefold() == matched_file.casefold():
                raise UnsafeRunnerCommand("Verify command may not execute the installer file.")
            if not executable.casefold().endswith(".exe"):
                raise UnsafeRunnerCommand("Verify executable must be an .exe under the fixed destination.")
            # Absolute path verification is safer than PATH-based verification.
            if not re.match(r"^[A-Za-z]:\\", executable):
                raise UnsafeRunnerCommand("Verify command must use an absolute Windows executable path.")
            if "&" in command and not uses_call_operator:
                raise UnsafeRunnerCommand("Invalid PowerShell call-operator usage.")
            return

        # Pre-install inspection is deliberately allowed to check the current runtime
        # by bare command name, but the name must correspond to the requested technology
        # where we have a known allowlist.
        if not _SIMPLE_COMMAND.fullmatch(executable):
            raise UnsafeRunnerCommand("Inspect executable must be a simple command name.")
        if allowed_names and executable.casefold() not in allowed_names:
            raise UnsafeRunnerCommand(
                f"Inspect executable '{executable}' is not an approved runtime command for {technology}."
            )

    def _parse_install_command(self, command: str) -> tuple[str, dict[str, list[str]]]:
        shape = _INSTALL_SHAPE.match(command)
        if not shape:
            raise UnsafeRunnerCommand("Install command must be a Start-Process invocation.")

        body = shape.group("body")
        if not _INSTALL_BODY.fullmatch(body):
            raise UnsafeRunnerCommand("Install command must contain only whitelisted quoted-string parameters.")

        params: dict[str, list[str]] = {}
        for name, values in _INSTALL_PARAM.findall(body):
            key = name.lower()
            if key not in _ALLOWED_PARAMS:
                raise UnsafeRunnerCommand(f"Start-Process parameter not allowed: -{name}")
            if key in params:
                raise UnsafeRunnerCommand(f"Duplicate Start-Process parameter: -{name}")

            tokens = re.findall(_QUOTED, values)
            parsed: list[str] = []
            for token in tokens:
                quote = token[0]
                content = token[1:-1]
                if not _SAFE_VALUE[quote].fullmatch(content):
                    raise UnsafeRunnerCommand("Unsafe characters in installer argument.")
                parsed.append(content)
            params[key] = parsed

        if len(params.get("filepath", [])) != 1:
            raise UnsafeRunnerCommand("Install command must provide exactly one literal -FilePath.")
        if not params.get("argumentlist"):
            raise UnsafeRunnerCommand("Install command must provide -ArgumentList explicitly.")
        for switch in ("wait", "passthru"):
            if switch not in params:
                raise UnsafeRunnerCommand(f"Install command must include -{switch.title()}.")
        return body.strip(), params

    def _validate_install_command(
        self,
        command: str,
        matched_file: str,
        available_files: list[str],
        technology: str,
    ) -> None:
        if matched_file not in available_files:
            raise UnsafeRunnerCommand("Approved installer is not in the discovered local package list.")
        if PureWindowsPath(matched_file).name != matched_file:
            raise UnsafeRunnerCommand("Installer selection must be a simple local filename.")

        self._base_safety_check(command)
        _, params = self._parse_install_command(command)
        file_path = params["filepath"][0]
        arguments = params["argumentlist"]
        joined = " ".join(arguments)
        expected_installer = self._installer_path(matched_file)

        # Exact local installer restriction.
        if matched_file.lower().endswith(".msi"):
            if file_path.casefold() != "msiexec.exe":
                raise UnsafeRunnerCommand("MSI installs must use msiexec.exe.")
            msi_match = re.search(
                r"(?i)(?:^|\s)/i\s+(?:'([^']+)'|\"([^\"]+)\"|([^\s]+))",
                joined,
            )
            msi_path = (msi_match.group(1) or msi_match.group(2) or msi_match.group(3)) if msi_match else None
            if not msi_path or not PureWindowsPath(msi_path) == PureWindowsPath(expected_installer):
                raise UnsafeRunnerCommand("msiexec.exe may install only the exact discovered MSI with /i.")
        elif PureWindowsPath(file_path).as_posix().casefold() != PureWindowsPath(expected_installer).as_posix().casefold():
            raise UnsafeRunnerCommand("Install command may execute only the exact discovered local installer.")

        # Synchronous execution is mandatory.
        if "wait" not in params or "passthru" not in params:
            raise UnsafeRunnerCommand("Install command must include -Wait and -PassThru.")

        # Destructive arguments are blocked.
        if re.search(r"(?i)uninstall|remove|delete|format|erase|(?:^|\s)/x(?:\s|$)", joined):
            raise UnsafeRunnerCommand("Install command contains uninstall/destructive intent.")

        # Remote/network sources are blocked.
        # Local Windows paths such as:
        #   C:\Users\Application_Packages\foo.exe
        # are allowed.
        #
        # Block:
        #   http://...
        #   https://...
        #   ftp://...
        #   file://...
        #   \\server\share\...
        #   //server/share/...
        if re.search(r"(?i)(?:https?|ftp|file)://", joined):
            raise UnsafeRunnerCommand("Install command may not use remote/network paths.")

        if re.search(r"(?<![A-Za-z]):\\\\", joined):
            raise UnsafeRunnerCommand("Install command may not use remote/network paths.")

        if re.search(r"(?m)(?:^|[\s'\"(])\\\\[^\\]", joined):
            raise UnsafeRunnerCommand("Install command may not use remote/network paths.")

        if re.search(r"(?m)(?:^|[\s'\"(])//[^/]", joined):
            raise UnsafeRunnerCommand("Install command may not use remote/network paths.")

        # Every installation must explicitly target the fixed destination.
        # Normalize separators so C:\Users\Agent_Installations and
        # C:\\Users\\Agent_Installations are treated as the same Windows path.
        fixed = self._normalize_install_argument(_INSTALL_DESTINATION)
        destination_args = [
            arg
            for arg in arguments
            if fixed in self._normalize_install_argument(arg)
        ]
        if not destination_args:
            raise UnsafeRunnerCommand(
                f"Install command must explicitly target the fixed installation destination: {_INSTALL_DESTINATION}"
            )

        # Technology-specific core components remain deterministic.
        # Normalize Windows path separators before comparing installer arguments so
        # harmless escaping differences do not cause false safety failures.
        required = _REQUIRED_INSTALL_ARGS.get(technology.strip().lower(), ())
        given = {self._normalize_install_argument(arg) for arg in arguments}
        missing = [
            arg
            for arg in required
            if self._normalize_install_argument(arg) not in given
        ]
        if missing:
            raise UnsafeRunnerCommand(
                "Installer command is missing required installation components: " + ", ".join(missing)
            )

    def validate_llm_commands(
        self,
        inspect_command: str,
        install_command: str,
        verify_command: str,
        matched_file: str,
        available_files: list[str],
        technology: str,
    ) -> None:
        self._validate_inspect_or_verify(
            inspect_command,
            require_fixed_destination=False,
            technology=technology,
            matched_file=matched_file,
        )
        self._validate_install_command(
            install_command,
            matched_file,
            available_files,
            technology,
        )
        self._validate_inspect_or_verify(
            verify_command,
            require_fixed_destination=True,
            technology=technology,
            matched_file=matched_file,
        )

    def _normalize_install_command(self, command: str) -> str:
        body, _ = self._parse_install_command(command)
        return f"$p = Start-Process {body}; $global:LASTEXITCODE = $p.ExitCode"

    # -------------------------------------------------------------------------
    # Azure Run Command execution
    # -------------------------------------------------------------------------

    def _get_compute_client(self) -> ComputeManagementClient:
        if self._client is None:
            self._client = ComputeManagementClient(
                credential=DefaultAzureCredential(exclude_cli_credential=True),
                subscription_id=self.subscription_id,
            )
        return self._client

    def _submit_run_command(self, script: str) -> Any:
        """Serialize Run Command calls and retry Azure 409 conflicts with backoff."""
        client = self._get_compute_client()
        with self._run_command_lock:
            for delay in (*self._RETRY_DELAYS, None):
                try:
                    return client.virtual_machines.begin_run_command(
                        resource_group_name=self.resource_group,
                        vm_name=self.vm_name,
                        parameters={
                            "commandId": "RunPowerShellScript",
                            "script": [script],
                        },
                    ).result()
                except HttpResponseError as exc:
                    busy = getattr(exc, "status_code", None) == 409 or "in progress" in str(exc).lower()
                    if not busy or delay is None:
                        raise
                    print(f"Azure VM Run Command is busy. Retrying in {delay}s...")
                    time.sleep(delay)
        raise RuntimeError("Unexpected Run Command retry state.")  # pragma: no cover

    def _execute_fixed_script(self, script: str, phase: str) -> dict[str, Any]:
        """Run adapter-owned PowerShell and parse the real exit code."""
        wrapper = _WRAPPER.replace("@@PHASE@@", phase).replace("@@SCRIPT@@", script)
        result = self._submit_run_command(wrapper)

        stdout_lines: list[str] = []
        stderr_lines: list[str] = []
        for item in result.value or []:
            is_stderr = (getattr(item, "code", "") or "").lower().endswith("/stderr")
            (stderr_lines if is_stderr else stdout_lines).append(getattr(item, "message", "") or "")

        stdout = "\n".join(stdout_lines).strip()
        stderr = "\n".join(stderr_lines).strip()
        match = re.search(r"__RUNNER_EXIT_CODE__=(-?\d+)", stdout)
        exit_code = int(match.group(1)) if match else 1

        if phase not in _RAW_OUTPUT_PHASES:
            stdout = self._redact(stdout)
            stderr = self._redact(stderr)

        return {
            "phase": phase,
            "status_code": exit_code,
            "stdout": stdout,
            "stderr": stderr,
            "success": exit_code == 0,
        }

    def _run_validated_command(self, command: str, phase: str) -> dict[str, Any]:
        self._base_safety_check(command)
        if phase == "install":
            command = self._normalize_install_command(command)
        return self._execute_fixed_script(command, phase)

    # -------------------------------------------------------------------------
    # Step 1: package discovery
    # -------------------------------------------------------------------------

    def _list_package_files(self) -> list[str]:
        script = _DISCOVERY_SCRIPT.replace("@@ROOT@@", self.package_root.replace("'", "''"))
        result = self._execute_fixed_script(script, "package_discovery")
        if not result["success"]:
            return []
        names = {
            line.strip()
            for line in result["stdout"].splitlines()
            if line.strip().lower().endswith((".exe", ".msi"))
        }
        return sorted(names, key=str.lower)

    # -------------------------------------------------------------------------
    # Steps 2-3: LLM planning
    # -------------------------------------------------------------------------

    def _ask_llm_for_commands(
        self,
        technology: str,
        required_version: str,
        available_files: list[str],
    ) -> dict[str, str]:
        rules = list(_LLM_RULES)
        required_args = _REQUIRED_INSTALL_ARGS.get(technology.strip().lower())
        if required_args:
            rules.append("For this technology, -ArgumentList MUST explicitly include all of: " + ", ".join(required_args))

        payload = {
            "technology": technology,
            "required_version": required_version,
            "package_root": self.package_root,
            "install_destination": _INSTALL_DESTINATION,
            "available_local_installer_files": available_files,
            "hard_rules": rules,
        }
        prompt = (
            "You generate PowerShell commands for environment-version remediation on a Windows self-hosted runner. "
            "You are NOT an execution authority: application code validates every command before it runs, "
            "and unsafe commands are rejected.\n\n"
            f"{_LLM_EXAMPLE}\n\n"
            "IMPORTANT DISTINCTION:\n"
            "- inspect_command checks the currently installed runtime before installation.\n"
            "- install_command executes the selected local installer.\n"
            "- verify_command checks the newly installed runtime by executing the executable INSIDE the fixed installation destination.\n\n"
            "The installer filename may appear ONLY in install_command.\n"
            "The verify executable path must be under the exact fixed installation destination.\n\n"
            "Generate commands from this input. Return JSON only.\n\n"
            + json.dumps(payload, indent=2)
        )

        text = re.sub(
            r"^```(?:json)?\s*|\s*```$",
            "",
            (get_azure_response(prompt) or "").strip(),
            flags=re.IGNORECASE,
        )
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise UnsafeRunnerCommand("LLM did not return valid JSON command output.") from exc

        if not isinstance(data, dict):
            raise UnsafeRunnerCommand("LLM command output must be a JSON object.")

        fields = ("matched_file", "inspect_command", "install_command", "verify_command")
        for field in fields:
            if not isinstance(data.get(field), str):
                raise UnsafeRunnerCommand(f"LLM output missing required field: {field}")

        commands = {field: data[field].strip() for field in fields}
        if not commands["matched_file"]:
            raise UnsafeRunnerCommand("LLM did not select a local installer.")
        if commands["matched_file"] not in available_files:
            raise UnsafeRunnerCommand("LLM did not select an installer from the local package list.")

        return commands

    def _plan_commands(
        self,
        technology: str,
        required_version: str,
        available_files: list[str],
    ) -> dict[str, str]:
        """LLM proposes -> code qualifies -> code validates -> execute later."""
        commands = self._ask_llm_for_commands(technology, required_version, available_files)
        commands["install_command"] = self._qualify_installer_path(
            commands["install_command"],
            commands["matched_file"],
        )
        self.validate_llm_commands(
            inspect_command=commands["inspect_command"],
            install_command=commands["install_command"],
            verify_command=commands["verify_command"],
            matched_file=commands["matched_file"],
            available_files=available_files,
            technology=technology,
        )

        print("=" * 80)
        print("LLM GENERATED COMMANDS (VALIDATED BEFORE EXECUTION)")
        print(self._redact(json.dumps(commands, indent=2)))
        print("=" * 80)
        return commands

    # -------------------------------------------------------------------------
    # Verification
    # -------------------------------------------------------------------------

    @staticmethod
    def _version_present(output: str, required: str) -> bool:
        required = required.strip().lstrip("vV")
        if not required:
            return False
        return re.search(rf"(?<![\d.]){re.escape(required)}(?!\d)", output or "") is not None

    def _run_post_install_checks(
        self,
        technology: str,
        verify_command: str,
    ) -> dict[str, Any]:
        """
        Code-owned post-install checks.

        The smoke tests use the exact executable path from verify_command, so they
        cannot accidentally fall back to another runtime earlier in PATH.
        """
        executable, _ = self._command_executable(verify_command)
        escaped_executable = executable.replace("'", "''")

        lines = [
            f"if (-not (Test-Path -LiteralPath '{escaped_executable}' -PathType Leaf)) {{ throw 'Verified executable was not found in fixed installation destination' }}",
            f"Write-Output ('RESOLVED=' + (Resolve-Path -LiteralPath '{escaped_executable}' -ErrorAction Stop).Path)",
        ]

        key = next((name for name in _SMOKE_TESTS if name in technology.lower()), None)
        for label, arguments in _SMOKE_TESTS.get(key, []):
            lines.append(f"Write-Output 'CHECK: {label}'")
            # Adapter-owned '&' is intentional here: it invokes the exact validated
            # executable path and does not come from the LLM.
            lines.append(f"& '{escaped_executable}' {arguments}")
            lines.append(f"if ($LASTEXITCODE -ne 0) {{ throw 'Smoke test failed: {label}' }}")

        result = self._execute_fixed_script("\n".join(lines), "post_install_checks")
        resolved = next(
            (
                line.split("=", 1)[1].strip()
                for line in result["stdout"].splitlines()
                if line.startswith("RESOLVED=")
            ),
            None,
        )
        return {
            "success": result["success"],
            "resolved_path": resolved,
            "smoke_ran": key is not None,
            "result": result,
        }

    def _verify_installation(
        self,
        base: dict[str, Any],
        commands: dict[str, str],
        inspect_result: dict[str, Any],
        install_result: dict[str, Any],
    ) -> dict[str, Any]:
        required_version = base["required_version"]
        verify_result = self._run_validated_command(commands["verify_command"], "verify")
        output = f"{verify_result['stdout']}\n{verify_result['stderr']}"
        version_ok = bool(
            verify_result["success"]
            and self._version_present(output, required_version)
        )

        post = self._run_post_install_checks(base["technology"], commands["verify_command"]) if version_ok else None
        success = bool(version_ok and post and post["success"])

        if success:
            message = "Environment updated and verified in fixed installation destination"
        elif not version_ok:
            message = (
                f"Verification failed: required version {required_version} was not confirmed "
                f"from an executable under {_INSTALL_DESTINATION}; build rerun was not attempted"
            )
        else:
            message = (
                "Required version was confirmed in the fixed destination but post-install "
                "checks failed; build rerun was not attempted"
            )

        return {
            **base,
            "success": success,
            "message": message,
            "matched_file": commands["matched_file"],
            "install_destination": _INSTALL_DESTINATION,
            "inspect": inspect_result,
            "install": install_result,
            "verify": verify_result,
            "verification_level": "functional" if post and post["smoke_ran"] else "version_only",
            "reboot_required": install_result["status_code"] in _REBOOT_CODES,
            "resolved_path": post["resolved_path"] if post else None,
            "post_install": post["result"] if post else None,
        }

    # -------------------------------------------------------------------------
    # Generic installer failure diagnostics
    # -------------------------------------------------------------------------

    def _collect_installer_failure_diagnostics(
        self,
        matched_file: str,
        install_result: dict[str, Any],
    ) -> dict[str, Any]:
        """Collect generic Windows evidence after a failed installer run."""
        script = (
            _DIAGNOSTICS_SCRIPT
            .replace("@@PATH@@", self._installer_path(matched_file).replace("'", "''"))
            .replace("@@NAME@@", matched_file.replace("'", "''"))
            .replace("@@EXIT@@", str(int(install_result.get("status_code", 1))))
        )
        try:
            result = self._execute_fixed_script(script, "installer_failure_diagnostics")
            json_line = next(
                (line.strip() for line in result["stdout"].splitlines() if line.strip().startswith("{")),
                None,
            )
            if result["success"] and json_line:
                data = json.loads(json_line)
                data["collection_success"] = True
            else:
                data = {
                    "collection_success": False,
                    "message": "Installer diagnostics could not be collected.",
                    "result": result,
                }
        except Exception as exc:
            data = {
                "collection_success": False,
                "message": f"Diagnostics collection failed: {type(exc).__name__}: {exc}",
            }
        return self._redact_deep(data)

    @staticmethod
    def _summarize_installer_failure_diagnostics(diagnostics: dict[str, Any]) -> list[str]:
        hints: list[str] = []
        free_space = diagnostics.get("free_space_gb")
        service = str(diagnostics.get("windows_installer_service_status") or "").lower()
        msiexec_count = diagnostics.get("msiexec_process_count")
        signature = str(diagnostics.get("installer_signature_status") or "").lower()

        if diagnostics.get("installer_exists") is False:
            hints.append("installer_file_missing")
        if isinstance(free_space, (int, float)) and free_space < 1.0:
            hints.append("low_disk_space")
        if diagnostics.get("pending_reboot") is True:
            hints.append("pending_reboot_detected")
        if str(diagnostics.get("installer_name", "")).lower().endswith(".msi") and service not in {"running", ""}:
            hints.append(f"windows_installer_service_{service}")
        if isinstance(msiexec_count, int) and msiexec_count > 0:
            hints.append("msiexec_process_still_running")
        if signature in {"invalid", "nottrusted", "hashmismatch"}:
            hints.append(f"installer_signature_{signature}")
        if diagnostics.get("recent_installer_logs"):
            hints.append("installer_log_captured")
        if diagnostics.get("recent_windows_events"):
            hints.append("relevant_windows_error_events_found")
        return hints or ["installer_returned_nonzero_exit_code"]

    def _failure(
        self,
        base: dict[str, Any],
        message: str,
        **extra: Any,
    ) -> dict[str, Any]:
        return {**base, "success": False, "message": message, **extra}

    def _installer_failure_result(
        self,
        base: dict[str, Any],
        commands: dict[str, str],
        inspect_result: dict[str, Any],
        install_result: dict[str, Any],
    ) -> dict[str, Any]:
        diagnostics = self._collect_installer_failure_diagnostics(
            commands["matched_file"],
            install_result,
        )
        hints = self._summarize_installer_failure_diagnostics(diagnostics)
        print("INSTALLER FAILURE DIAGNOSTICS")
        print(json.dumps(diagnostics, indent=2, default=str)[:4000])
        return self._failure(
            base,
            f"Installer failed with exit code {install_result['status_code']}; verification and build rerun were not attempted. "
            f"Generic diagnostics: {', '.join(hints)}.",
            matched_file=commands["matched_file"],
            install_destination=_INSTALL_DESTINATION,
            inspect=inspect_result,
            install=install_result,
            installer_failure_diagnostics=diagnostics,
            diagnostic_hints=hints,
        )

    # -------------------------------------------------------------------------
    # Main flow
    # -------------------------------------------------------------------------

    def resolve_version_compatibility(
        self,
        runner_name: str,
        technology: str,
        required_version: str,
    ) -> dict[str, Any]:
        if not runner_name or not technology or not required_version:
            return {
                "success": False,
                "message": "runner_name, technology and required_version are required",
            }

        base: dict[str, Any] = {
            "runner_name": runner_name,
            "technology": technology,
            "required_version": required_version,
            "install_destination": _INSTALL_DESTINATION,
        }

        try:
            available_files = self._list_package_files()
        except Exception as exc:
            return self._failure(
                base,
                f"Package discovery failed: {type(exc).__name__}: {self._redact(exc)}",
            )

        if not available_files:
            return self._failure(
                base,
                f"No approved local .exe/.msi installer files were found on target VM under {self.package_root}",
                available_files=[],
            )

        base["available_files"] = available_files

        try:
            commands = self._plan_commands(technology, required_version, available_files)

            inspect_result = self._run_validated_command(commands["inspect_command"], "inspect")

            install_result = self._run_validated_command(commands["install_command"], "install")
            if not (
                install_result["success"]
                or install_result["status_code"] in _REBOOT_CODES
            ):
                return self._installer_failure_result(
                    base,
                    commands,
                    inspect_result,
                    install_result,
                )

            return self._verify_installation(
                base,
                commands,
                inspect_result,
                install_result,
            )

        except UnsafeRunnerCommand as exc:
            return self._failure(
                base,
                f"Safety validation blocked execution: {self._redact(exc)}",
            )
        except Exception as exc:
            return self._failure(
                base,
                f"Runner remediation failed: {type(exc).__name__}: {self._redact(exc)}",
            )
