import os
import requests
from contextlib import contextmanager
from fastapi import APIRouter, BackgroundTasks, Request
from pydantic import BaseModel
from typing import Any
from datetime import datetime, timezone

from failure_agent import Failure_Agent
from vida.utils.preprocess import try_parse_json
from vida.utils.request_context import task_id_ctx
from vida.models.requests.Agent_Task_requests import AgentTaskDetailsCreateRequest, AgentTaskDetailsUpdateRequest
from vida.database.database import sessionlocal
from vida.utils.crud_ops import AgentTaskOps as ato
from vida.utils.logger import get_logger
from vida.utils.llm import get_azure_response

logger = get_logger(__name__)
router = APIRouter()


# ── DB context ────────────────────────────────────────────────────────────────

@contextmanager
def get_db():
    db = sessionlocal()
    try:
        yield db
    finally:
        db.close()


# ── ADO helpers ───────────────────────────────────────────────────────────────

def _ado_headers() -> dict:
    import base64
    pat = os.getenv("ADO_PAT", "")
    token = base64.b64encode(f":{pat}".encode()).decode()
    return {"Authorization": f"Basic {token}", "Content-Type": "application/json"}


def _fetch_build_timeline(org_url: str, project: str, build_id: int) -> list[dict]:
    url = f"{org_url.rstrip('/')}/{project}/_apis/build/builds/{build_id}/Timeline?api-version=7.1"
    try:
        resp = requests.get(url, headers=_ado_headers(), timeout=15)
        resp.raise_for_status()
        return resp.json().get("records", [])
    except Exception as e:
        logger.warning(f"[webhook] Failed to fetch timeline for build {build_id}: {e}")
        return []


def _fetch_log_content(log_url: str) -> str:
    try:
        resp = requests.get(log_url, headers=_ado_headers(), timeout=15)
        resp.raise_for_status()
        return resp.text
    except Exception as e:
        logger.warning(f"[webhook] Failed to fetch log from {log_url}: {e}")
        return ""


def _fetch_failed_logs(org_url: str, project: str, build_id: int, records: list[dict]) -> str:
    failed_records = [
        r for r in records
        if r.get("result") in ("failed", "canceled") and r.get("log")
    ]
    if not failed_records:
        failed_records = [r for r in records if r.get("log") and r.get("type") == "Task"]
    log_parts = []
    for record in failed_records[:5]:
        log_url = record.get("log", {}).get("url")
        if not log_url:
            continue
        content = _fetch_log_content(log_url)
        lines = content.strip().splitlines()
        trimmed = "\n".join(lines[-100:]) if len(lines) > 100 else content
        log_parts.append(f"### Task: {record.get('name', 'Unknown')} (result={record.get('result')})\n{trimmed}")
    return "\n\n".join(log_parts)


# ── GitHub helpers ────────────────────────────────────────────────────────────

def _github_headers() -> dict:
    token = os.getenv("GITHUB_PAT", "")
    return {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}


def _fetch_github_failed_jobs(repo: str, run_id: int) -> tuple[list[dict], str]:
    """Fetch failed jobs and their log snippets for a GitHub Actions run."""
    jobs_url = f"https://api.github.com/repos/{repo}/actions/runs/{run_id}/jobs"
    try:
        resp = requests.get(jobs_url, headers=_github_headers(), timeout=15)
        resp.raise_for_status()
        jobs = resp.json().get("jobs", [])
    except Exception as e:
        logger.warning(f"[webhook] Failed to fetch GitHub jobs for run {run_id}: {e}")
        return [], ""

    failed_jobs = [j for j in jobs if j.get("conclusion") == "failure"]
    log_parts = []
    for job in failed_jobs[:5]:
        job_id = job.get("id")
        log_url = f"https://api.github.com/repos/{repo}/actions/jobs/{job_id}/logs"
        try:
            log_resp = requests.get(log_url, headers=_github_headers(), timeout=15, allow_redirects=True)
            lines = log_resp.text.strip().splitlines()
            trimmed = "\n".join(lines[-100:]) if len(lines) > 100 else log_resp.text
            log_parts.append(f"### Job: {job.get('name')} (conclusion=failure)\n{trimmed}")
        except Exception as e:
            logger.warning(f"[webhook] Failed to fetch log for job {job_id}: {e}")

    return failed_jobs, "\n\n".join(log_parts)


# ── Prompt builders ───────────────────────────────────────────────────────────

def _build_ado_prompt(payload: dict, records: list[dict], failed_logs: str) -> str:
    resource = payload.get("resource", {})
    definition = resource.get("definition", {})
    project = resource.get("project", {})
    repo = resource.get("repository", {})
    detailed_msg = payload.get("detailedMessage", {}).get("text", "")
    failed_steps = [
        f"  - {r['name']} (type={r.get('type')}, result={r.get('result')})"
        for r in records if r.get("result") in ("failed", "canceled")
    ]
    branch = resource.get("sourceBranch", "").replace("refs/heads/", "")
    return f"""An Azure DevOps pipeline has failed. Analyze the failure and take corrective action.

## Build Details
- Source       : Azure DevOps
- Project      : {project.get('name')}
- Pipeline     : {definition.get('name')} (ID: {definition.get('id')})
- Build Number : {resource.get('buildNumber')}
- Build ID     : {resource.get('id')}
- Branch       : {branch}
- Repository   : {repo.get('name')} ({repo.get('url')})
- Triggered by : {resource.get('requestedFor', {}).get('displayName')}
- Start Time   : {resource.get('startTime')}
- Finish Time  : {resource.get('finishTime')}
- Build URL    : {resource.get('_links', {}).get('web', {}).get('href')}

## Failure Summary
{detailed_msg.strip()}

## Failed Steps
{chr(10).join(failed_steps) if failed_steps else '  (none identified)'}

## Failed Step Logs
{failed_logs if failed_logs else '(logs unavailable)'}

## Instructions
1. Identify the root cause from the logs and failed steps above.
2. Determine which sub-agent (ADO, YAML, GitHub, Terraform) should handle the fix.
3. Delegate to the appropriate agent and apply the fix.
4. Report what was found and what action was taken.
"""


def _build_github_prompt(payload: dict, failed_jobs: list[dict], failed_logs: str) -> str:
    run = payload.get("workflow_run", {})
    repo = payload.get("repository", {}).get("full_name", "unknown")
    failed_job_names = [
        f"  - {j.get('name')} (steps failed: {sum(1 for s in j.get('steps', []) if s.get('conclusion') == 'failure')})"
        for j in failed_jobs
    ]
    return f"""A GitHub Actions workflow has failed. Analyze the failure and take corrective action.

## Workflow Details
- Source      : GitHub Actions
- Repository  : {repo}
- Workflow    : {run.get('name')}
- Run ID      : {run.get('id')}
- Branch      : {run.get('head_branch')}
- Commit SHA  : {run.get('head_sha')}
- Triggered by: {run.get('triggering_actor', {}).get('login', 'unknown')}
- Run URL     : {run.get('html_url')}
- Conclusion  : {run.get('conclusion')}

## Failed Jobs
{chr(10).join(failed_job_names) if failed_job_names else '  (none identified)'}

## Failed Job Logs
{failed_logs if failed_logs else '(logs unavailable)'}

## Instructions
1. Identify the root cause from the logs and failed jobs above.
2. Determine which sub-agent (ADO, YAML, GitHub, Terraform) should handle the fix.
3. Delegate to the appropriate agent and apply the fix.
4. Report what was found and what action was taken.
"""


# ── Shared background task ────────────────────────────────────────────────────

async def _run_failure_agent(prompt: str, task_name: str):
    final_prompt = str(get_azure_response(
        text=f"remove the redundant data and give only the relevant error information for the failure agent to solve: {prompt}"
    ))

    with get_db() as db:
        task_id = ato().add_task(db=db, task=AgentTaskDetailsCreateRequest(
            agent_id=2,
            task_status="pending",
            task_prompt=prompt,
            task_name=task_name,
            start_time=datetime.now(timezone.utc),
        ))

    if not task_id:
        logger.error(f"[webhook] Failed to create task: {task_name}")
        return

    task_id_ref = task_id_ctx.set(task_id)
    try:
        agent = Failure_Agent.get_instance()
        response, _ = await agent.run(prompt=final_prompt, task_id=task_id)
        if response:
            _, _ = try_parse_json(response.text)
            with get_db() as db:
                ato().update_task(db=db, task_id=task_id, task=AgentTaskDetailsUpdateRequest(
                    task_status="success", end_time=datetime.now(timezone.utc)
                ))
            logger.info(f"[webhook] Task '{task_name}' processed successfully. task_id={task_id}")
        else:
            with get_db() as db:
                ato().update_task(db=db, task_id=task_id, task=AgentTaskDetailsUpdateRequest(
                    task_status="failed", end_time=datetime.now(timezone.utc),
                    issue="No response from failure agent"
                ))
    except Exception as e:
        logger.error(f"[webhook] Error processing task '{task_name}': {e}", exc_info=True)
        with get_db() as db:
            ato().update_task(db=db, task_id=task_id, task=AgentTaskDetailsUpdateRequest(
                task_status="failed", end_time=datetime.now(timezone.utc), issue=str(e)
            ))
    finally:
        task_id_ctx.reset(task_id_ref)


async def _process_ado_webhook(payload: dict):
    resource = payload.get("resource", {})
    project_name = resource.get("project", {}).get("name", "unknown")
    build_id = resource.get("id")
    org_url = os.getenv("ADO_ORG_URL", "https://dev.azure.com/VAMDOJOPractice")
    logger.info(f"[webhook/ado] Processing build {build_id}, project='{project_name}'")
    records = _fetch_build_timeline(org_url, project_name, build_id)
    failed_logs = _fetch_failed_logs(org_url, project_name, build_id, records)
    prompt = _build_ado_prompt(payload, records, failed_logs)
    await _run_failure_agent(prompt, task_name=f"webhook-ado-{build_id}")


async def _process_github_webhook(payload: dict):
    run = payload.get("workflow_run", {})
    repo = payload.get("repository", {}).get("full_name", "unknown")
    run_id = run.get("id")
    logger.info(f"[webhook/github] Processing run {run_id}, repo='{repo}'")
    failed_jobs, failed_logs = _fetch_github_failed_jobs(repo, run_id)
    prompt = _build_github_prompt(payload, failed_jobs, failed_logs)
    await _run_failure_agent(prompt, task_name=f"webhook-github-{run_id}")


# ── Unified endpoint ──────────────────────────────────────────────────────────

@router.post("/webhook")
async def unified_webhook(request: Request, background_tasks: BackgroundTasks):
    payload = await request.json()
    headers = request.headers
    github_event = headers.get("X-GitHub-Event")

    # ── GitHub Actions workflow_run failure ───────────────────────────────
    if github_event == "workflow_run":
        conclusion = payload.get("workflow_run", {}).get("conclusion")
        if conclusion != "failure":
            return {"status": "ignored", "reason": f"workflow_run conclusion='{conclusion}', only 'failure' handled"}
        # background_tasks.add_task(_process_github_webhook, payload) # Note: update to queue
        logger.info(f"[webhook] Accepted GitHub workflow_run failure")
        return {"status": "accepted", "source": "github"}

    # ── ADO build.complete failure ────────────────────────────────────────
    if "eventType" in payload and "resource" in payload:
        if payload.get("eventType") != "build.complete":
            return {"status": "ignored", "reason": f"ADO eventType '{payload.get('eventType')}' not handled"}
        resource = payload.get("resource", {})
        if resource.get("result") != "failed":
            return {"status": "ignored", "reason": f"ADO build result='{resource.get('result')}', only 'failed' handled"}
        # background_tasks.add_task(_process_ado_webhook, payload) # Note: update to queue
        logger.info(f"[webhook] Accepted ADO build.complete failure")
        return {"status": "accepted", "source": "ado"}

    logger.warning("[webhook] Unknown payload source")
    return {"status": "ignored", "reason": "Could not determine webhook source"}
