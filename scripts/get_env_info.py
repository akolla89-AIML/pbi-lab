"""
Log in as the service principal and print, for each lab workspace:
workspace ID, lakehouse ID, and SQL analytics endpoint.
Credentials come from environment variables, so nothing secret lives in this file.
"""
import os
import requests
from azure.identity import ClientSecretCredential

TARGETS = {"Sales-DEV", "Sales-QA"}
BASE = "https://api.fabric.microsoft.com/v1"

cred = ClientSecretCredential(
    tenant_id=os.environ["AZURE_TENANT_ID"],
    client_id=os.environ["AZURE_CLIENT_ID"],
    client_secret=os.environ["AZURE_CLIENT_SECRET"],
)
token = cred.get_token("https://api.fabric.microsoft.com/.default").token
headers = {"Authorization": f"Bearer {token}"}

resp = requests.get(f"{BASE}/workspaces", headers=headers)
resp.raise_for_status()
visible = {w["displayName"]: w["id"] for w in resp.json()["value"]}

for name in sorted(TARGETS):
    print(f"\n== {name} ==")
    if name not in visible:
        print("  NOT VISIBLE to the service principal -> check Manage access (1.1)")
        continue
    ws_id = visible[name]
    print(f"  workspace_id : {ws_id}")
    lh = requests.get(f"{BASE}/workspaces/{ws_id}/lakehouses", headers=headers)
    lh.raise_for_status()
    for item in lh.json().get("value", []):
        sql = item.get("properties", {}).get("sqlEndpointProperties", {})
        print(f"  lakehouse    : {item['displayName']}  ({item['id']})")
        print(f"  sql_endpoint : {sql.get('connectionString')}  [status: {sql.get('provisioningStatus')}]")