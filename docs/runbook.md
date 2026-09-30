# Sales CI/CD runbook

Manual steps the pipelines cannot do for themselves. Each section says when the step is needed, how to do it, and how to confirm it worked.

## 1. Bind the lakehouse credential (once per workspace)

**When:** after `Sales` is deployed to a workspace for the first time (Sales-DEV, Sales-QA), or whenever the pipeline's "Refresh semantic model" step fails with a credentials error.

**Why:** fabric-cicd deploys the model's definition only. The credential the model uses to read the lakehouse SQL endpoint is stored in the workspace, not in Git, so a newly deployed model has none and its first refresh fails. Later deploys update the definition and keep the stored credential.

**Who:** a workspace Admin.

**Steps**

1. In Fabric, open the workspace, find the semantic model `Sales`, and open **… > Settings**.
2. The model is owned by `sp-fabric-cicd`, because the pipeline created it, so its connection settings are read-only. Select **Take over** to become the owner. The pipeline can still deploy and refresh the model, because the service principal keeps its workspace role.
3. Expand **Data source credentials** and select **Edit credentials** for the SQL endpoint source. If the page shows **Gateway and cloud connections** with a *Maps to* list instead, open the cloud connection there and edit its credentials.
4. Set the authentication method to **OAuth2** and the privacy level to **Organizational**, then **Sign in**.
5. Do not select *Refresh now*. Re-run the failed pipeline run so the pipeline proves the fix.

**Confirm:** the pipeline's refresh step prints `PASS refresh completed`, and the model's **Refresh history** shows a completed refresh started through the API.

**Redo when:** the model is deleted and redeployed, ownership changes, or the owner's credential stops working (password change, account disabled, long inactivity).

**Log**

| Date | Workspace | Done by | Pipeline run |
|---|---|---|---|
| | Sales-DEV | | |

**Production note:** here the credential belongs to a person. In production it would be a shared cloud connection that authenticates as a service principal or workspace identity. fabric-cicd would bind the model to it at deploy time, through the `semantic_model_binding` section of `parameter.yml`, which removes this manual step.