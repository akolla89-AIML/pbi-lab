"""Pre-refresh checks against the lakehouse, run before the semantic model is refreshed.

Runs every tests/source/*.sql file against the lakehouse's SQL analytics endpoint,
which is the same endpoint the model refreshes from. Each query returns one row
per violation; zero rows means the check passed. Any violation exits 1, so a
refresh that is certain to fail (for example a duplicate dimension key) never starts.

The SQL endpoint is looked up from the workspace through the Fabric API, not taken
from a variable, so the check always runs against the workspace it guards.

Environment variables:
  AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET   service principal
  FABRIC_ENVIRONMENT, FABRIC_WORKSPACE_ID, FABRIC_WORKSPACE_NAME
  LAKEHOUSE_NAME   lakehouse to check, e.g. lh_sales
  ODBC_DRIVER      optional; default "ODBC Driver 18 for SQL Server"

Usage:
  python scripts/check_source.py
"""

import os
import struct
import sys
from pathlib import Path

import requests
from azure.identity import ClientSecretCredential

from deploy import FABRIC_API, FABRIC_SCOPE, guard_workspace, require_env

REPO_ROOT = Path(__file__).resolve().parents[1]
CHECKS_DIR = REPO_ROOT / "tests" / "source"
SQL_SCOPE = "https://database.windows.net/.default"
SQL_COPT_SS_ACCESS_TOKEN = 1256  # ODBC connection attribute that carries an Entra access token


def find_sql_endpoint(credential, workspace_id: str, lakehouse_name: str) -> str:
    """Return the lakehouse's SQL analytics endpoint host from the Fabric API."""
    token = credential.get_token(FABRIC_SCOPE).token
    headers = {"Authorization": f"Bearer {token}"}
    response = requests.get(f"{FABRIC_API}/workspaces/{workspace_id}/lakehouses", headers=headers, timeout=30)
    response.raise_for_status()
    match = next((lh for lh in response.json().get("value", []) if lh["displayName"] == lakehouse_name), None)
    if match is None:
        sys.exit(f"ERROR: no lakehouse named '{lakehouse_name}' in workspace {workspace_id}.")
    detail = requests.get(
        f"{FABRIC_API}/workspaces/{workspace_id}/lakehouses/{match['id']}", headers=headers, timeout=30
    )
    detail.raise_for_status()
    sql = (detail.json().get("properties") or {}).get("sqlEndpointProperties") or {}
    if sql.get("provisioningStatus") != "Success" or not sql.get("connectionString"):
        sys.exit(f"ERROR: the SQL endpoint of '{lakehouse_name}' is not ready ({sql.get('provisioningStatus')}).")
    return sql["connectionString"]


def connect(credential, server: str, database: str):
    """Open an ODBC connection to the SQL endpoint, signed in as the service principal."""
    import pyodbc  # imported here so the DAX runner never needs an ODBC driver installed

    driver = os.environ.get("ODBC_DRIVER", "ODBC Driver 18 for SQL Server")
    if driver not in pyodbc.drivers():
        sys.exit(f"ERROR: ODBC driver '{driver}' is not installed. Installed: {pyodbc.drivers()}")
    token = credential.get_token(SQL_SCOPE).token.encode("utf-16-le")
    token_struct = struct.pack(f"<I{len(token)}s", len(token), token)
    connection_string = (
        f"Driver={{{driver}}};Server=tcp:{server},1433;Database={database};"
        "Encrypt=yes;TrustServerCertificate=no;Connection Timeout=60"
    )
    return pyodbc.connect(connection_string, attrs_before={SQL_COPT_SS_ACCESS_TOKEN: token_struct})


def main() -> None:
    environment = require_env("FABRIC_ENVIRONMENT")
    workspace_id = require_env("FABRIC_WORKSPACE_ID")
    workspace_name = require_env("FABRIC_WORKSPACE_NAME")
    lakehouse_name = require_env("LAKEHOUSE_NAME")
    checks = sorted(CHECKS_DIR.glob("*.sql"))
    if not checks:
        sys.exit(f"ERROR: no .sql files found in {CHECKS_DIR}.")

    credential = ClientSecretCredential(
        tenant_id=require_env("AZURE_TENANT_ID"),
        client_id=require_env("AZURE_CLIENT_ID"),
        client_secret=require_env("AZURE_CLIENT_SECRET"),
    )
    guard_workspace(credential, workspace_id, workspace_name, environment)
    server = find_sql_endpoint(credential, workspace_id, lakehouse_name)
    print(f"Checking {lakehouse_name} in {workspace_name} via {server}\n")

    import pyodbc  # for pyodbc.Error; connect() has already checked the driver

    failed = 0
    with connect(credential, server, lakehouse_name) as connection:
        cursor = connection.cursor()
        for check in checks:
            try:
                cursor.execute(check.read_text(encoding="utf-8"))
                columns = [c[0] for c in cursor.description]
                rows = cursor.fetchall()
            except pyodbc.Error as exc:
                failed += 1
                print(f"ERROR  source/{check.stem} could not run: {exc}")
                if os.environ.get("GITHUB_ACTIONS") == "true":
                    print(f"::error title=source/{check.stem}::could not run: {exc}")
                continue
            if not rows:
                print(f"PASS   source/{check.stem}")
                continue
            failed += 1
            print(f"FAIL   source/{check.stem}: {len(rows)} violation(s)")
            print("       " + " | ".join(columns))
            for row in rows:
                print("       " + " | ".join(str(v) for v in row))
            if os.environ.get("GITHUB_ACTIONS") == "true":
                first = ", ".join(f"{c}={v}" for c, v in zip(columns, rows[0]))
                print(f"::error title=source/{check.stem}::{len(rows)} violation(s), first: {first}")

    if failed:
        sys.exit(f"\n{failed} source check(s) failed. Do not refresh until the data is fixed.")
    print("\nAll source checks passed. Safe to refresh.")


if __name__ == "__main__":
    main()