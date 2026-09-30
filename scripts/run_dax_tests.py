"""Run the DAX assertion tests in tests/ against a deployed semantic model.

Every tests/<suite>/*.dax file holds one DAX query that returns exactly one row
with two columns:
  Pass    TRUE when the assertion holds
  Detail  short text showing the values that were checked

Suites and what a failure means:
  code       The DAX is wrong, in every environment. Exit code 1 (blocks).
  integrity  The data in this workspace has a problem. Reported as a warning,
             exit code 0 (alerts).
A test that cannot run at all (DAX error, missing Pass column) exits 1 in either
suite: a broken test is not a data finding.

Environment variables (same names as deploy.py):
  AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET   service principal
  FABRIC_ENVIRONMENT, FABRIC_WORKSPACE_ID, FABRIC_WORKSPACE_NAME

Usage:
  python scripts/run_dax_tests.py --list            # show the tests; no sign-in
  python scripts/run_dax_tests.py --bundle          # print all tests as one query for DAX query view
  python scripts/run_dax_tests.py --smoke           # sign in and run one constant query
  python scripts/run_dax_tests.py --suite code      # code tests only
  python scripts/run_dax_tests.py                   # code, then integrity
"""

import argparse
import io
import os
import sys
from pathlib import Path

import pyarrow as pa
import requests
from azure.identity import ClientSecretCredential

from deploy import FABRIC_API, FABRIC_SCOPE, guard_workspace, require_env

REPO_ROOT = Path(__file__).resolve().parents[1]
TESTS_DIR = REPO_ROOT / "tests"
SUITES = ["code", "integrity"]
POWERBI_API = "https://api.powerbi.com/v1.0/myorg"
POWERBI_SCOPE = "https://analysis.windows.net/powerbi/api/.default"
SMOKE_QUERY = 'EVALUATE ROW ( "Pass", TRUE (), "Detail", "Connected and query executed" )'


class TestError(Exception):
    """The test could not produce a Pass/Detail result."""


# ---------- Finding and describing tests (no network) ----------

def discover(suite: str) -> list[Path]:
    """All .dax files in tests/<suite>/, in name order (T1, T2, ... T8, T8b)."""
    return sorted((TESTS_DIR / suite).glob("*.dax"))


def describe(path: Path) -> str:
    """The first comment line of a test file, without the leading //."""
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("//"):
            return line.strip().lstrip("/").strip()
    return ""


def test_id(path: Path) -> str:
    return f"{path.parent.name}/{path.stem}"


# ---------- Talking to Fabric and Power BI ----------

def find_model_id(credential, workspace_id: str, model_name: str) -> str:
    """Look up the semantic model's ID by its display name."""
    token = credential.get_token(FABRIC_SCOPE).token
    response = requests.get(
        f"{FABRIC_API}/workspaces/{workspace_id}/semanticModels",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    response.raise_for_status()
    models = response.json().get("value", [])
    for model in models:
        if model["displayName"] == model_name:
            return model["id"]
    names = ", ".join(m["displayName"] for m in models) or "none"
    sys.exit(f"ERROR: no semantic model named '{model_name}' in the workspace (found: {names}).")


def run_query(credential, workspace_id: str, model_id: str, query: str) -> list[pa.Table]:
    """Send one DAX query to the Execute DAX Queries API and return its result tables."""
    token = credential.get_token(POWERBI_SCOPE).token
    response = requests.post(
        f"{POWERBI_API}/groups/{workspace_id}/datasets/{model_id}/executeDaxQueries",
        headers={"Authorization": f"Bearer {token}"},
        json={"query": query, "queryTimeout": 120},
        timeout=180,
    )
    if response.status_code in (401, 403):
        raise TestError(
            f"HTTP {response.status_code}: the service principal may not query this model. Check the tenant "
            "settings 'Semantic Model Execute Queries REST API' and 'XMLA endpoints', and its workspace role. "
            f"Response: {response.text[:300]}"
        )
    if not response.ok:
        raise TestError(f"HTTP {response.status_code}: {response.text[:500]}")
    return parse_arrow(response.content)


def parse_arrow(content: bytes) -> list[pa.Table]:
    """Read the concatenated Arrow streams in a response; raise on an error rowset."""
    stream = io.BytesIO(content)
    tables = []
    while stream.tell() < len(content):
        try:
            reader = pa.ipc.open_stream(stream)
            table = reader.read_all()
        except pa.ArrowInvalid:
            break
        metadata = {k.decode(): v.decode() for k, v in (reader.schema.metadata or {}).items()}
        if metadata.get("IsError") == "true":
            raise TestError(f"DAX error {metadata.get('FaultCode')}: {metadata.get('FaultString')}")
        tables.append(table)
    return tables


# ---------- Judging a result ----------

def plain_name(column: str) -> str:
    """'[Pass]' or 'Table[Pass]' -> 'Pass'."""
    return column.rsplit("[", 1)[-1].rstrip("]")


def judge(tables: list[pa.Table]) -> tuple[bool, str]:
    """Apply the test contract: one table, one row, a Pass column and a Detail column."""
    if len(tables) != 1 or tables[0].num_rows != 1:
        shape = [t.num_rows for t in tables]
        raise TestError(f"expected one table with one row, got row counts {shape}")
    row = {plain_name(name): values[0] for name, values in tables[0].to_pydict().items()}
    if "Pass" not in row:
        raise TestError(f"no Pass column; columns were {list(row)}")
    passed = row["Pass"] is True or str(row["Pass"]).lower() == "true"
    return passed, str(row.get("Detail") or "")


# ---------- Reporting ----------

def report(status: str, tid: str, detail: str) -> None:
    print(f"{status:<6} {tid:<45} {detail}")
    if os.environ.get("GITHUB_ACTIONS") == "true" and status != "PASS":
        level = "warning" if status == "WARN" else "error"
        print(f"::{level} title={tid}::{detail}")


def write_step_summary(rows: list[tuple[str, str, str]]) -> None:
    """On GitHub Actions, add a results table to the job's summary page."""
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    lines = ["| Result | Test | Detail |", "|---|---|---|"]
    lines += [f"| {s} | `{t}` | {d.replace('|', '/')} |" for s, t, d in rows]
    with open(summary_path, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


# ---------- Main ----------

def main() -> None:
    parser = argparse.ArgumentParser(description="Run DAX tests against a deployed semantic model.")
    parser.add_argument("--suite", choices=SUITES, action="append",
                        help="Suite to run; repeat for more than one. Default: all.")
    parser.add_argument("--model", default="Sales", help="Semantic model display name (default: Sales).")
    parser.add_argument("--list", action="store_true", help="List the tests and exit.")
    parser.add_argument("--bundle", action="store_true",
                        help="Print every test as one query to paste into DAX query view, and exit.")
    parser.add_argument("--smoke", action="store_true", help="Run one constant query to prove access, and exit.")
    args = parser.parse_args()
    suites = args.suite or SUITES
    tests = [(suite, path) for suite in suites for path in discover(suite)]

    if args.list:
        for suite, path in tests:
            print(f"{test_id(path):<45} {describe(path)}")
        return
    if args.bundle:
        for suite, path in tests:
            print(f"// ===== {test_id(path)} =====")
            print(path.read_text(encoding="utf-8").strip() + "\n")
        return
    if not tests and not args.smoke:
        sys.exit(f"ERROR: no .dax files found under {TESTS_DIR} for suites {suites}.")

    # Same configuration and guard as deploy.py, so tests only ever run against the intended workspace.
    environment = require_env("FABRIC_ENVIRONMENT")
    workspace_id = require_env("FABRIC_WORKSPACE_ID")
    workspace_name = require_env("FABRIC_WORKSPACE_NAME")
    credential = ClientSecretCredential(
        tenant_id=require_env("AZURE_TENANT_ID"),
        client_id=require_env("AZURE_CLIENT_ID"),
        client_secret=require_env("AZURE_CLIENT_SECRET"),
    )
    guard_workspace(credential, workspace_id, workspace_name, environment)
    model_id = find_model_id(credential, workspace_id, args.model)
    print(f"Testing model '{args.model}' ({model_id}) in {workspace_name}\n")

    if args.smoke:
        tests = [("smoke", None)]

    results = []
    for suite, path in tests:
        tid = test_id(path) if path else "smoke"
        query = path.read_text(encoding="utf-8") if path else SMOKE_QUERY
        try:
            passed, detail = judge(run_query(credential, workspace_id, model_id, query))
            status = "PASS" if passed else ("WARN" if suite == "integrity" else "FAIL")
        except (TestError, requests.RequestException) as exc:
            status, detail = "ERROR", str(exc)
        report(status, tid, detail)
        results.append((status, tid, detail))

    write_step_summary(results)
    counts = {s: sum(1 for r in results if r[0] == s) for s in ("PASS", "FAIL", "WARN", "ERROR")}
    print("\n" + ", ".join(f"{n} {s.lower()}" for s, n in counts.items()))
    if counts["FAIL"] or counts["ERROR"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
