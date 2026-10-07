from __future__ import annotations

import base64
import os
import time
from urllib.parse import quote

import requests
from agent_framework import tool
from pydantic import Field
from typing import Annotated

from failure_config import ado_org_url, ado_pat


ADO_BUILD_API_VERSION = "7.2-preview.8"


def _ado_headers() -> dict[str, str]:
    if not ado_pat:
        raise RuntimeError("ADO_PAT is not configured.")
    token = base64.b64encode(f":{ado_pat}".encode("utf-8")).decode("ascii")
    return {
        "Authorization": f"Basic {token}",
        "Content-Type": "application/json",
    }


def _build_url(project: str, build_id: int) -> str:
    if not ado_org_url:
        raise RuntimeError("ADO_ORG_URL is not configured.")
    org = ado_org_url.rstrip("/")
    return f"{org}/{quote(project, safe='')}/_apis/build/builds/{build_id}"


@tool(
    name="ADO_Rerun_Failed_Build",
    description=(
        "Reruns a specific failed Azure DevOps build using the Azure DevOps build "
        "retry operation. This is a rerun of the existing failed build, not a new "
        "pipeline run. After requesting the rerun, it polls the returned build until "
        "completion or timeout and returns the final result."
    ),
    approval_mode="never_require",
)
def ado_rerun_failed_build(
    project: Annotated[str, Field(description="Azure DevOps project name containing the failed build.")],
    build_id: Annotated[int, Field(description="Build ID of the failed Azure DevOps build to rerun.")],
    timeout_seconds: Annotated[int, Field(description="Maximum time to wait for the rerun to finish. Default 1800 seconds.")]=1800,
    poll_interval_seconds: Annotated[int, Field(description="Seconds between build status checks.")]=10,
) -> str:
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be greater than zero.")
    if poll_interval_seconds <= 0:
        raise ValueError("poll_interval_seconds must be greater than zero.")

    headers = _ado_headers()
    url = _build_url(project, build_id)
    rerun_url = f"{url}?retry=true&api-version={ADO_BUILD_API_VERSION}"

    response = requests.patch(
        rerun_url,
        headers=headers,
        data="",
        timeout=30,
    )
    response.raise_for_status()
    rerun_build = response.json()
    rerun_build_id = int(rerun_build.get("id", build_id))

    deadline = time.monotonic() + timeout_seconds
    last_build = rerun_build

    # The retry response may contain a different build ID. Poll that returned
    # build ID, not the original failed build.
    rerun_url_base = _build_url(project, rerun_build_id)

    while time.monotonic() < deadline:
        status_url = f"{rerun_url_base}?api-version={ADO_BUILD_API_VERSION}"
        status_response = requests.get(status_url, headers=headers, timeout=30)
        status_response.raise_for_status()
        last_build = status_response.json()

        status = str(last_build.get("status", "")).lower()
        if status == "completed":
            result = str(last_build.get("result", "unknown")).lower()
            import json
            return json.dumps({
                "build_id": rerun_build_id,
                "status": status,
                "result": result,
                "build_url": last_build.get("_links", {}).get("web", {}).get("href"),
                "success": result == "succeeded",
            })

        time.sleep(poll_interval_seconds)

    return __import__("json").dumps({
        "build_id": rerun_build_id,
        "status": last_build.get("status"),
        "result": last_build.get("result"),
        "success": False,
        "timed_out": True,
        "message": f"Rerun did not complete within {timeout_seconds} seconds.",
        "build_url": last_build.get("_links", {}).get("web", {}).get("href"),
    })
