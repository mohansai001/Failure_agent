# FAILURE_AGENT

A reactive recovery agent invoked by the Coordinator when a DevOps pipeline fails. It analyzes the failure, reasons about probable solutions using an LLM, and can re-invoke other agents to apply fixes.

---

## Responsibilities

- Analyze pipeline failure context and error details
- Use LLM reasoning to identify probable root causes and solutions
- Re-invoke GitHub, YAML, or Terraform agents to apply corrective actions
- Provide structured recovery recommendations

---

## Architecture

```
POST /failure_agent
        │
        ▼
  Failure_Agent (LLM)
        │
        ├── ProbableSolutionsAnalyzer   → LLM reasons over failure + agent capabilities
        ├── Github_Agent (HTTP)         → re-invokes GITHUB_AGENT for fixes
        ├── Yaml_Agent (HTTP)           → re-invokes YAML_AGENT for pipeline fixes
        └── Terraform_Agent (HTTP)      → re-invokes TERRAFORM_AGENT for IaC fixes
```

The `ProbableSolutionsAnalyzer` is always called first to reason about what went wrong before delegating to a fix agent.

---

## File Structure

```
FAILURE_AGENT/
├── main.py                     # FastAPI app entry point
├── api.py                      # Route: POST /failure_agent
├── failure_agent.py            # Failure_Agent class definition
├── failure_config.py           # Loads sub-agent URLs from .env
├── tools/
│   ├── agent_calls.py          # @tool HTTP wrappers for sub-agents
│   ├── probable_solutions.py   # ProbableSolutionsAnalyzer tool
│   └── __init__.py
├── .env
└── requirements.txt
```

---

## Environment Variables

```env
# Sub-agent service URLs
Github_agent_URL=http://localhost:8001/github_agent
YAML_agent_URL=http://localhost:8002/yaml_agent
Terraform_agent_URL=http://localhost:8003/terraform_agent

# Azure OpenAI
AZURE_OPENAI_ENDPOINT=<your_azure_openai_endpoint>
AZURE_OPENAI_API_KEY=<your_azure_openai_api_key>
AZURE_OPENAI_DEPLOYMENT=<your_deployment_name>
```

---

## API

### `POST /failure_agent`

**Request Body:**
```json
{
  "prompt": "The GitHub Actions workflow ci.yml failed on branch main in repo my-org/my-app with error: 'No module named flask'",
  "pat_token": "<github_personal_access_token>"
}
```

**Response:**
```json
{
  "response": "Failure agent executed successfully",
  "raw": { ... },
  "is_json": false,
  "output": "The failure is caused by a missing dependency. Recommended fix: add 'flask' to requirements.txt and re-trigger the workflow."
}
```

---

## Available Tools

### `Probable_Solutions_Analyzer`

Analyzes the failure prompt and generates probable solutions using LLM reasoning.

| Parameter | Description |
|---|---|
| `prompt` | Full failure context including error message, repo, branch, and workflow details |

Internally builds a capability summary of all available agents and uses a `GeneratorPrompt` template to guide the LLM's analysis.

---

### `Github_Agent` (HTTP delegate)

Re-invokes the GitHub Agent to perform corrective GitHub operations (e.g., commit a fix, re-trigger a workflow).

| Parameter | Description |
|---|---|
| `prompt` | Instruction for the GitHub Agent |

Passes the PAT token from `github_pat_ctx` context variable.

---

### `Yaml_Agent` (HTTP delegate)

Re-invokes the YAML Agent to regenerate or fix a broken pipeline YAML.

| Parameter | Description |
|---|---|
| `prompt` | Instruction for the YAML Agent |

---

### `Terraform_Agent` (HTTP delegate)

Re-invokes the Terraform Agent to fix or regenerate IaC configuration.

| Parameter | Description |
|---|---|
| `prompt` | Instruction for the Terraform Agent |

---

## Failure Analysis Flow

```
1. Coordinator detects failure → calls failure_agent_tool_call(prompt)
2. Failure Agent receives full failure context
3. ProbableSolutionsAnalyzer runs:
   - Loads descriptions of all available agents
   - Renders a structured analysis prompt
   - LLM returns probable causes + recommended actions
4. Failure Agent LLM decides:
   - Return analysis only, OR
   - Delegate fix to Github/Yaml/Terraform agent
5. Response returned to Coordinator
```

---

## Setup & Run

```bash
cd agents/FAILURE_AGENT

pip install -r requirements.txt

cp .env.example .env   # fill in values

uvicorn main:app --host 0.0.0.0 --port 8004 --reload
```

---

## Example Usage

```bash
# Report a workflow failure for analysis
curl -X POST http://localhost:8004/failure_agent \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "Workflow python-ci.yml failed on branch main in repo my-org/my-app. Error: requirements.txt not found in root directory.",
    "pat_token": "<your_github_pat>"
  }'
```

---

## Security Note

Avoid hardcoding PAT tokens in code or test files. Always use `.env` files or a secrets manager. The PAT token is propagated via Python `contextvars` (`github_pat_ctx`) and should never be logged or printed.
