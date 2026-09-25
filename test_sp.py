import os, requests
from azure.identity import ClientSecretCredential
cred = ClientSecretCredential(os.environ["AZURE_TENANT_ID"], os.environ["AZURE_CLIENT_ID"], os.environ["AZURE_CLIENT_SECRET"])
token = cred.get_token("https://api.fabric.microsoft.com/.default").token
r = requests.get("https://api.fabric.microsoft.com/v1/workspaces", headers={"Authorization": f"Bearer {token}"})
r.raise_for_status()
for ws in r.json()["value"]:
    print(ws["displayName"], ws["id"])