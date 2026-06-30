from fastapi import APIRouter
from failure_agent import Failure_Agent
from vida.models.requests.Agents_requests import failure_agent_request
from vida.utils.preprocess import try_parse_json
from vida.utils.request_context import github_pat_ctx


router = APIRouter()

@router.post("/failure_agent")
async def read_failure(request: failure_agent_request ):
    git_token = request.pat_token 
    if git_token:
        token_ref = github_pat_ctx.set(git_token)
    else:
        return {"message": "No git token provided"}
    try:
        agent = Failure_Agent.get_instance()
        
        response = await agent.run(prompt=request.prompt)
        if response:
            output, is_json = try_parse_json(response.text)
            return {
                    "response": f"Failure agent executed successfully",
                    "raw": response,
                    "is_json": is_json,
                    "output": output
                }

        print("Failed to get response from agent")         
        return {"message": "Failed to get response from agent"}
    finally:
        github_pat_ctx.reset(token_ref)