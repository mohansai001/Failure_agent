from fastapi import FastAPI
from api import router as failure_agent_router
from webhook_router import router as webhook_router
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Adjust this to your needs
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(failure_agent_router)
app.include_router(webhook_router)