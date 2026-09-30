"""Refresh the Sales semantic model in one Fabric workspace and wait for the result.

Uses the Power BI enhanced refresh API: the POST returns a refresh ID, which this
script polls until the refresh completes or fails. The exit code tells GitHub
Actions which it was.

Environment variables (same names as deploy.py):
  AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET   service principal
  FABRIC_ENVIRONMENT, FABRIC_WORKSPACE_ID, FABRIC_WORKSPACE_NAME

Usage:
  python scripts/refresh_model.py             # full refresh; wait for the result
  python scripts/refresh_model.py --dry-run   # sign in, guard, find the model; no refresh
"""

import argparse
import os
import sys
import time

import requests
from azure.identity import ClientSecretCredential

from deploy import guard_workspace, require_env
from run_dax_tests import POWERBI_API, POWERBI_SCOPE, find_model_id

MODEL_NAME = "Sales"
POLL_SECONDS = 15
TIMEOUT_MINUTES = 30
FINAL_STATES = {"Completed", "Failed", "Cancelled", "Disabled", "TimedOut"}
RUNBOOK_HINT = (
    "The model has no working lakehouse credential. "
    "Bind it once in this workspace (docs/runbook.md, section 1), then re-run."
)


# ---------- GitHub Actions output (no-ops on your laptop) ----------

def annotate(level: str, message: str) -> None:
    """Show a one-line message as an ::error or ::warning annotation on the run page."""
    if os.environ.get("GITHUB_ACTIONS") == "true":
        print(f"::{level} title=Refresh::{message}")


def write_summary(lines: list[str]) -> None:
    """Append lines to the job summary page."""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n\n")


# ---------- Power BI enhanced refresh API ----------

def auth_headers(credential) -> dict:
    """Bearer token for the Power BI API. azure-identity caches it between calls."""
    token = credential.get_token(POWERBI_SCOPE).token
    return {"Authorization": f"Bearer {token}"}


def start_refresh(credential, workspace_id: str, model_id: str) -> str:
    """Start a full refresh and return the URL that reports its progress."""
    url = f"{POWERBI_API}/groups/{workspace_id}/datasets/{model_id}/refreshes"
    body = {"type": "Full", "commitMode": "transactional", "retryCount": 0}
    response = requests.post(url, headers=auth_headers(credential), json=body, timeout=60)
    if response.status_code == 409:
        sys.exit("ERROR: a refresh of this model is already running. Let it finish, then re-run.")
    if response.status_code != 202:
        sys.exit(f"ERROR: refresh request rejected (HTTP {response.status_code}): {response.text[:500]}")

    location = response.headers.get("Location")
    if location:
        return location
    refresh_id = response.headers.get("x-ms-request-id") or response.headers.get("RequestId")
    if not refresh_id:
        sys.exit("ERROR: the refresh started, but the response gave no ID to track it by.")
    return f"{url}/{refresh_id}"


def wait_for_refresh(credential, status_url: str) -> tuple[str, dict]:
    """Poll until the refresh reaches a final state or the time limit runs out."""
    deadline = time.monotonic() + TIMEOUT_MINUTES * 60
    last_state = None
    details: dict = {}
    while True:
        response = requests.get(status_url, headers=auth_headers(credential), timeout=60)
        if response.status_code == 404:
            state = "NotStarted"  # the service can take a moment to register a new refresh
        else:
            response.raise_for_status()
            details = response.json()
            state = details.get("extendedStatus") or details.get("status") or "Unknown"

        if state != last_state:
            print(f"  status: {state}", flush=True)
            last_state = state
        if state in FINAL_STATES:
            return state, details
        if time.monotonic() > deadline:
            return f"still {state} after {TIMEOUT_MINUTES} min", details
        time.sleep(POLL_SECONDS)


def failure_messages(details: dict) -> list[str]:
    """The service's error messages for a failed refresh, one string each."""
    messages = [
        f"{m.get('code', '')}: {m.get('message', '')}".strip(": ")
        for m in details.get("messages") or []
    ]
    if not messages and details.get("serviceExceptionJson"):
        messages.append(details["serviceExceptionJson"])
    return messages


def main() -> None:
    parser = argparse.ArgumentParser(description="Refresh the Sales model and wait for the result.")
    parser.add_argument("--dry-run", action="store_true",
                        help="sign in, run the guard and find the model, but don't refresh")
    args = parser.parse_args()

    environment = require_env("FABRIC_ENVIRONMENT")
    workspace_id = require_env("FABRIC_WORKSPACE_ID")
    workspace_name = require_env("FABRIC_WORKSPACE_NAME")
    credential = ClientSecretCredential(
        tenant_id=require_env("AZURE_TENANT_ID"),
        client_id=require_env("AZURE_CLIENT_ID"),
        client_secret=require_env("AZURE_CLIENT_SECRET"),
    )

    guard_workspace(credential, workspace_id, workspace_name, environment)
    print(f"Guard passed: {workspace_name} ({environment})")
    model_id = find_model_id(credential, workspace_id, MODEL_NAME)

    if args.dry_run:
        print(f"Dry run: would refresh '{MODEL_NAME}' ({model_id}) in {workspace_name}.")
        return

    started = time.monotonic()
    status_url = start_refresh(credential, workspace_id, model_id)
    print(f"Refresh of '{MODEL_NAME}' in {workspace_name} started; checking every {POLL_SECONDS}s.")
    state, details = wait_for_refresh(credential, status_url)
    minutes = (time.monotonic() - started) / 60

    if state == "Completed":
        print(f"PASS refresh completed in {minutes:.1f} min")
        write_summary([f"### Refresh: {workspace_name}", f"Completed in {minutes:.1f} min."])
        return

    messages = failure_messages(details)
    print(f"FAIL refresh ended as {state} after {minutes:.1f} min")
    for text in messages:
        print(f"  {text}")
    credential_problem = any("credential" in text.lower() for text in messages)
    if credential_problem:
        print(RUNBOOK_HINT)
    annotate("error", RUNBOOK_HINT if credential_problem else f"Refresh ended as {state}. See the step log.")
    write_summary([f"### Refresh: {workspace_name}", f"**{state}** after {minutes:.1f} min."]
                  + [f"- {text}" for text in messages])
    sys.exit(1)


if __name__ == "__main__":
    main()