# Dependency and Version Compatibility Skill

## Purpose

Use this skill when analyzing pipeline failures that may be caused by dependency, package, SDK, runtime, or tool version compatibility issues.

The goal is to determine whether the failure is caused by a version mismatch in the execution environment and provide the information needed by the Failure Agent to decide the next action.

This skill is for **analysis only**. Do not make code changes and do not directly execute commands or modify runners.

## What to identify

When analyzing a failure, check for:

* The technology involved, such as .NET, .NET SDK, Node.js, npm, NuGet, Java, Python, Terraform, or another build/runtime dependency.
* The version mentioned in the error.
* The required or compatible version, when it can be determined.
* The currently installed or detected version, when it is present in the logs.
* The runner name or identifier, when available in the failure information.
* The environment in which the failure occurred, when available.
* Whether the problem is an environment/version compatibility issue or a code/project dependency issue.

## Version compatibility issue

Classify the failure as a version compatibility issue when the evidence indicates that the pipeline requires a different version of a dependency, SDK, runtime, package manager, or build tool than the version available in the execution environment.

Examples:

* The pipeline requires .NET SDK 8, but the runner has .NET SDK 6.
* A required Node.js version is not available on the runner.
* The npm version is incompatible with the required Node.js version.
* A required NuGet/runtime/tool version is missing or incompatible.
* A build tool reports that the installed version is older than the supported version.

## Code-level dependency issue

Do not classify the problem as a runner/environment update when the failure requires changing the application's source code or project dependency references.

Examples:

* Changing a version in package.json.
* Changing a PackageReference version in a .csproj file.
* Changing application code to support a newer API.
* Updating source code to use a different package version.

For these cases, do not recommend modifying the code as part of the runner resolution flow.

## Runner information

When possible, identify the runner from the failure logs or pipeline context.

Capture:

* Runner name or identifier.
* Runner platform or type, if known.
* Operating system, if known.
* Relevant labels, capabilities, or environment information, if available.

Do not assume that a runner is GitHub, Azure DevOps, or another platform if the information does not prove it.

If the runner type cannot be determined, clearly state that it is unknown and identify what information is missing.

## Hosted vs self-hosted consideration

If the runner type is known, distinguish between platform-hosted and self-hosted execution environments.

For platform-hosted runners, treat an environment version change as a temporary preparation for the retriggered pipeline rather than a permanent machine update.

For self-hosted runners, an environment version change may be a normal persistent update to the runner.

If the runner type is unknown, do not assume whether the change should be temporary or persistent.

## Required analysis result

For a possible dependency/version failure, provide the following information:

### Code change required

State whether resolving the failure requires a code or project dependency change:

* Yes
* No
* Unknown

### Issue type

Use one of:

* version_compatibility
* code_dependency
* insufficient_information
* other

### Technology

Identify the affected technology or dependency.

### Required version

Provide the required or compatible version, if known.

### Current version

Provide the current detected version, if known.

### Runner

Provide the runner name or identifier, if known.

### Runner type

Provide the runner type if it can be determined. Otherwise state: Unknown.

### Recommended action

For a version compatibility issue, state that the execution environment may need to be updated to the required compatible version.

Do not recommend source-code changes.

### Missing information

Clearly list any information required before the Failure Agent can safely continue.

## Output mapping

When analyzing a dependency/version compatibility issue, include the
following information in the existing JSON fields where applicable:

* issue type → issue_summary
* affected technology → issue_summary / root_cause
* required version → issue_summary / root_cause / solution
* current version → issue_summary / root_cause
* runner name → issue_summary / prerequisites
* runner type → issue_summary / prerequisites
* code change required → issue_summary / rationale
* recommended environment action → solution / implementation_steps

## Important rules

* Use evidence from the error, logs, stack trace, and available context.
* Do not guess a required version when the evidence does not support it.
* Do not assume the runner type.
* Do not change application code.
* Do not modify package references in source files.
* Do not directly connect to or modify a runner.
* Do not directly trigger or retrigger a pipeline.
* The Probable Solutions Analyzer only diagnoses the problem and provides the information needed by the Failure Agent.
* The Failure Agent decides which tool or specialist capability should handle the next step.
