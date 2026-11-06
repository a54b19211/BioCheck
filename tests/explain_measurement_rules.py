"""Print, for every matrix sample and wrong-final negative, the v4 status and each non-match field with its reason
(a review aid: the status alone can be right for the wrong reason)."""
import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from test_measurement_rules import MATRIX, ROWS, request_of  # noqa: E402

from app import measurement_rules  # noqa: E402

for row in MATRIX["rows"]:
    if row["group"] == "negative" and row.get("kind") != "wrong_final_at_real_span":
        continue
    if row["group"] == "negative":
        base = ROWS[row["of"]]
        final = copy.deepcopy(base["final"])
        for key, value in row["change"].items():
            final[key.split(".", 1)[1]] = value
        body = request_of(base, final)
    else:
        body = request_of(row)
    body.pop("contract_version")
    out = measurement_rules.check(body)["items"][0]
    print(row["id"], out["status"], json.dumps({k: v.get("reason", "") for k, v in out["fields"].items()
                                                 if v["result"] != "match"}, ensure_ascii=False))
