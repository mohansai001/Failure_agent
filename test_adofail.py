import requests
import base64
from dotenv import load_dotenv
import os

load_dotenv()

pat = os.getenv("ADO_PAT")
org_url = os.getenv("ADO_ORG_URL")
project = "<your_project_name>"
pipeline_id = <your_pipeline_id>

token = base64.b64encode(f":{pat}".encode()).decode()
headers = {"Authorization": f"Basic {token}", "Content-Type": "application/json"}

response = requests.post(
    f"{org_url}/{project}/_apis/build/builds?api-version=7.1",
    headers=headers,
    json={"definition": {"id": pipeline_id}}
)
print(response.status_code)
print(response.json())
