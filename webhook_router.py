import os
import re
import requests
from contextlib import contextmanager
from fastapi import APIRouter, BackgroundTasks, HTTPException
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

logger = get_logger(__name__)
router = APIRouter()


# ── ADO REST helpers ──────────────────────────────────────────────────────────

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
        data = resp.json()
        return data.get("records", [])
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
    """Fetch log content for failed/errored timeline records."""
    failed_records = [
        r for r in records
        if r.get("result") in ("failed", "canceled") and r.get("log")
    ]
    if not failed_records:
        # fallback: fetch all task-level logs
        failed_records = [r for r in records if r.get("log") and r.get("type") == "Task"]

    log_parts = []
    for record in failed_records[:5]:  # cap at 5 to avoid huge prompts
        log_info = record.get("log", {})
        log_url = log_info.get("url")
        if not log_url:
            continue
        content = _fetch_log_content(log_url)
        # trim to last 100 lines to keep prompt size reasonable
        lines = content.strip().splitlines()
        trimmed = "\n".join(lines[-100:]) if len(lines) > 100 else content
        log_parts.append(
            f"### Task: {record.get('name', 'Unknown')} (result={record.get('result')})\n{trimmed}"
        )

    return "\n\n".join(log_parts)


# ── Prompt builder ────────────────────────────────────────────────────────────

def _build_prompt(payload: dict, records: list[dict], failed_logs: str) -> str:
    resource = payload.get("resource", {})
    definition = resource.get("definition", {})
    project = resource.get("project", {})
    repo = resource.get("repository", {})
    detailed_msg = payload.get("detailedMessage", {}).get("text", "")

    failed_steps = [
        f"  - {r['name']} (type={r.get('type')}, result={r.get('result')})"
        for r in records if r.get("result") in ("failed", "canceled")
    ]
    failed_steps_text = "\n".join(failed_steps) if failed_steps else "  (none identified)"

    branch = resource.get("sourceBranch", "").replace("refs/heads/", "")

    return f"""An Azure DevOps build has failed. Analyze the failure and take corrective action.

## Build Details
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

## ADO Failure Summary
{detailed_msg.strip()}

## Failed Pipeline Steps
{failed_steps_text}

## Failed Step Logs
{failed_logs if failed_logs else "(logs unavailable)"}

## Instructions
1. Identify the root cause from the logs and failed steps above.
2. Determine which sub-agent (ADO, YAML, GitHub, Terraform) should handle the fix.
3. Delegate to the appropriate agent and apply the fix.
4. Report what was found and what action was taken.
"""


# ── DB context ────────────────────────────────────────────────────────────────

@contextmanager
def get_db():
    db = sessionlocal()
    try:
        yield db
    finally:
        db.close()


# ── Background task ───────────────────────────────────────────────────────────

async def _process_webhook(payload: dict):
    resource = payload.get("resource", {})
    project_name = resource.get("project", {}).get("name", "unknown")
    build_id = resource.get("id")
    org_url = os.getenv("ADO_ORG_URL", "https://dev.azure.com/VAMDOJOPractice")

    logger.info(f"[webhook] Processing build.complete for build {build_id}, project='{project_name}'")

    # Gather context from ADO
    records = _fetch_build_timeline(org_url, project_name, build_id)
    failed_logs = _fetch_failed_logs(org_url, project_name, build_id, records)
    prompt = _build_prompt(payload, records, failed_logs)

    # Create task record
    with get_db() as db:
        task_id = ato().add_task(db=db, task=AgentTaskDetailsCreateRequest(
            agent_id=2,
            task_status="pending",
            task_prompt=prompt[:200],
            task_name=f"webhook-build-{build_id}",
            start_time=datetime.now(timezone.utc),
        ))

    if not task_id:
        logger.error(f"[webhook] Failed to create task for build {build_id}")
        return

    task_id_ref = task_id_ctx.set(task_id)
    try:
        agent = Failure_Agent.get_instance()
        response = await agent.run(prompt=prompt, task_id=task_id)

        if response:
            _, _ = try_parse_json(response.text)
            with get_db() as db:
                ato().update_task(db=db, task_id=task_id, task=AgentTaskDetailsUpdateRequest(
                    task_status="success", end_time=datetime.now(timezone.utc)
                ))
            logger.info(f"[webhook] Build {build_id} processed successfully. task_id={task_id}")
        else:
            with get_db() as db:
                ato().update_task(db=db, task_id=task_id, task=AgentTaskDetailsUpdateRequest(
                    task_status="failed", end_time=datetime.now(timezone.utc),
                    issue="No response from failure agent"
                ))
            logger.warning(f"[webhook] No response from failure agent for build {build_id}")

    except Exception as e:
        logger.error(f"[webhook] Error processing build {build_id}: {e}", exc_info=True)
        with get_db() as db:
            ato().update_task(db=db, task_id=task_id, task=AgentTaskDetailsUpdateRequest(
                task_status="failed", end_time=datetime.now(timezone.utc), issue=str(e)
            ))
    finally:
        task_id_ctx.reset(task_id_ref)


# ── Endpoint ──────────────────────────────────────────────────────────────────

class ADOWebhookPayload(BaseModel):
    model_config = {"extra": "allow"}

    eventType: str
    resource: Any = None


@router.post("/ado/webhook")
async def ado_webhook(payload: ADOWebhookPayload, background_tasks: BackgroundTasks):
    if payload.eventType != "build.complete":
        logger.info(f"[webhook] Ignoring event type: {payload.eventType}")
        return {"status": "ignored", "reason": f"eventType '{payload.eventType}' not handled"}

    resource = payload.resource or {}
    if isinstance(resource, dict) and resource.get("result") != "failed":
        result = resource.get("result", "unknown")
        logger.info(f"[webhook] Build result='{result}', skipping non-failed build")
        return {"status": "ignored", "reason": f"build result is '{result}', only 'failed' builds are processed"}

    background_tasks.add_task(_process_webhook, payload.model_dump())
    logger.info(f"[webhook] Accepted build.complete webhook, queued for processing")
    return {"status": "accepted", "message": "Webhook received, failure agent triggered in background"}
