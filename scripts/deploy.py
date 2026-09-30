"""Deploy the Sales semantic model and report from src/ to one Fabric workspace.

Everything comes from environment variables, so this runs unchanged on your
laptop and on a GitHub Actions runner:

  AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET   service principal (secrets)
  FABRIC_ENVIRONMENT     DEV or QA; must match a key in src/parameter.yml
  FABRIC_WORKSPACE_ID    GUID of the target workspace
  FABRIC_WORKSPACE_NAME  expected display name of that workspace (guard check)

Usage:
  python scripts/deploy.py            # publish only
  python scripts/deploy.py --prune    # publish, then delete models/reports no longer in src/
"""

import argparse
import os
import sys
from pathlib import Path

import base64
import json

import requests
from azure.identity import ClientSecretCredential
from fabric_cicd import FabricWorkspace, publish_all_items, unpublish_all_orphan_items

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
ALLOWED_ENVIRONMENTS = {"DEV", "QA"}
ITEM_TYPES = ["SemanticModel", "Report"]
FABRIC_API = "https://api.fabric.microsoft.com/v1"
FABRIC_SCOPE = "https://api.fabric.microsoft.com/.default"


def require_env(name: str) -> str:
    """Return a required environment variable or stop with a clear message."""
    value = os.environ.get(name, "").strip()
    if not value:
        sys.exit(f"ERROR: environment variable {name} is not set.")
    return value

def token_identity(token: str) -> tuple:
    """Return (app ID, identity type) from an access token's payload. Display only; not a security check."""
    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    claims = json.loads(base64.urlsafe_b64decode(payload))
    return claims.get("appid") or claims.get("azp", "unknown"), claims.get("idtyp", "not in token")

def guard_workspace(credential, workspace_id: str, expected_name: str, environment: str) -> None:
    """Refuse to deploy unless the workspace ID really is the workspace we expect."""
    token = credential.get_token(FABRIC_SCOPE).token
    app_id, identity_type = token_identity(token)
    print(f"Signed in as app {app_id} (identity type: {identity_type})")
    response = requests.get(
        f"{FABRIC_API}/workspaces/{workspace_id}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    if response.status_code in (401, 403, 404):
        sys.exit(
            f"ERROR: the service principal cannot access workspace {workspace_id} "
            f"(HTTP {response.status_code}). Check its workspace role."
        )
    response.raise_for_status()

    actual_name = response.json()["displayName"]
    if actual_name != expected_name:
        sys.exit(
            f"ERROR: workspace {workspace_id} is '{actual_name}', "
            f"but FABRIC_WORKSPACE_NAME says '{expected_name}'. Not deploying."
        )
    if not actual_name.endswith(f"-{environment}"):
        sys.exit(
            f"ERROR: FABRIC_ENVIRONMENT is {environment}, "
            f"but the target workspace is '{actual_name}'. Not deploying."
        )
    print(f"Guard passed: {environment} -> '{actual_name}' ({workspace_id})")


def main() -> None:
    parser = argparse.ArgumentParser(description="Deploy src/ to a Fabric workspace.")
    parser.add_argument(
        "--prune",
        action="store_true",
        help="After publishing, delete semantic models and reports in the workspace that are not in src/.",
    )
    args = parser.parse_args()

    # 1. Read and validate configuration before touching anything.
    environment = require_env("FABRIC_ENVIRONMENT")
    if environment not in ALLOWED_ENVIRONMENTS:
        sys.exit(f"ERROR: FABRIC_ENVIRONMENT must be one of {sorted(ALLOWED_ENVIRONMENTS)}, got '{environment}'.")
    workspace_id = require_env("FABRIC_WORKSPACE_ID")
    workspace_name = require_env("FABRIC_WORKSPACE_NAME")
    if not (SRC_DIR / "parameter.yml").is_file():
        sys.exit(f"ERROR: {SRC_DIR / 'parameter.yml'} not found.")

    # 2. Sign in as the service principal.
    credential = ClientSecretCredential(
        tenant_id=require_env("AZURE_TENANT_ID"),
        client_id=require_env("AZURE_CLIENT_ID"),
        client_secret=require_env("AZURE_CLIENT_SECRET"),
    )

    # 3. Make sure we are pointed at the right workspace.
    guard_workspace(credential, workspace_id, workspace_name, environment)

    # 4. Publish. fabric-cicd applies the parameter.yml block for this environment.
    workspace = FabricWorkspace(
        workspace_id=workspace_id,
        environment=environment,
        repository_directory=str(SRC_DIR),
        item_type_in_scope=ITEM_TYPES,
        token_credential=credential,
    )
    publish_all_items(workspace)

    # 5. Optional clean-up of items that were removed from the repo.
    if args.prune:
        unpublish_all_orphan_items(workspace)

    print(f"Deploy to {workspace_name} ({environment}) finished.")


if __name__ == "__main__":
    main()