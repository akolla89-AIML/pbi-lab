"""Offline check for src/parameter.yml: env keys present, regex matches once."""
import re
import sys
from pathlib import Path

import yaml

SRC = Path(__file__).resolve().parents[1] / "src"
REQUIRED_ENVS = {"DEV", "QA"}

params = yaml.safe_load((SRC / "parameter.yml").read_text(encoding="utf-8"))
ok = True

for i, rule in enumerate(params.get("find_replace", []), start=1):
    missing = REQUIRED_ENVS - set(rule["replace_value"])
    if missing:
        print(f"Rule {i}: missing environment key(s) {sorted(missing)}")
        ok = False

    if str(rule.get("is_regex", "")).lower() == "true":
        target = SRC / rule["file_path"].lstrip("/\\")
        matches = re.findall(rule["find_value"], target.read_text(encoding="utf-8"))
        if len(matches) != 1:
            print(f"Rule {i}: expected 1 match in {target.name}, found {len(matches)}")
            ok = False
        else:
            print(f"Rule {i}: OK, will replace '{matches[0]}' in {target.name}")

print("parameter.yml check passed" if ok else "parameter.yml check FAILED")
sys.exit(0 if ok else 1)