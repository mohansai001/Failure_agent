from contextlib import contextmanager
from fastapi import APIRouter
from failure_agent import Failure_Agent
from vida.models.requests.Agents_requests import failure_agent_request
from vida.models.requests.Agent_Task_requests import AgentTaskDetailsCreateRequest, AgentTaskDetailsUpdateRequest
from vida.database.database import sessionlocal

from vida.utils.preprocess import try_parse_json
from vida.utils.request_context import github_pat_ctx, task_id_ctx

from vida.utils.crud_ops import AgentTaskOps as ato
from datetime import datetime, timezone


router = APIRouter()

@contextmanager
def get_db():
    db = sessionlocal()
    try:
        yield db
    finally:
        db.close()

@router.post("/failure_agent")
async def read_failure(request: failure_agent_request):
    token_ref = None
    task_id_ref = None
    git_token = request.pat_token
    task_id = request.task_id

    if not task_id:
        payload = AgentTaskDetailsCreateRequest(
            agent_id = 2,
            task_status = "pending",
            task_prompt = request.prompt,
            task_name = request.prompt[:20],
            start_time = datetime.now(timezone.utc)
        )
        with get_db() as db:
            task_id = ato().add_task(db=db, task=payload)
        if not task_id:
            with get_db() as db:
                ato().update_task(
                    db=db,
                    task_id=task_id,
                    task=AgentTaskDetailsUpdateRequest(
                        task_status="failed",
                        end_time=datetime.now(timezone.utc),
                        issue="Failed to create task"
                    )
                )
            return {"message": "Failed to create task"}
        else:
            task_id_ref = task_id_ctx.set(task_id)
    else:
        task_id_ref = task_id_ctx.set(task_id)

    if git_token:
        token_ref = github_pat_ctx.set(git_token)
    else:
        with get_db() as db:
            ato().update_task(
                db=db,
                task_id=task_id,
                task=AgentTaskDetailsUpdateRequest(
                    task_status="failed",
                    end_time=datetime.now(timezone.utc),
                    issue="No git token provided"
                )
            )
        return {"message": "No git token provided"}

    try:
        agent = Failure_Agent.get_instance()
        response = await agent.run(prompt=request.prompt, task_id=task_id)
        if response:
            output, is_json = try_parse_json(response.text)
            with get_db() as db:
                ato().update_task(
                    db=db,
                    task_id=task_id,
                    task=AgentTaskDetailsUpdateRequest(
                        task_status="success",
                        end_time=datetime.now(timezone.utc),
                    )
                )
            return {
                "response": "Failure agent executed successfully",
                "raw": response,
                "is_json": is_json,
                "output": output
            }

        print("Failed to get response from agent")
        with get_db() as db:
            ato().update_task(
                db=db,
                task_id=task_id,
                task=AgentTaskDetailsUpdateRequest(
                    end_time=datetime.now(timezone.utc),
                    task_status="failed",
                    issue="Failed to get response from agent"
                )
            )
        return {"message": "Failed to get response from agent"}

    except Exception as e:
        with get_db() as db:
            ato().update_task(
                db=db,
                task_id=task_id,
                task=AgentTaskDetailsUpdateRequest(
                    task_status="failed",
                    end_time=datetime.now(timezone.utc),
                    issue=str(e)
                )
            )
        raise

    finally:
        if token_ref:
            github_pat_ctx.reset(token_ref)
        if task_id_ref:
            task_id_ctx.reset(task_id_ref)
