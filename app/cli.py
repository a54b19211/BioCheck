"""python -m app.cli verify request.json  — run one operation without HTTP, print the JSON result."""
import argparse
import json
import sys
from pathlib import Path

from . import core
from .service import GenerateRequest, ReviseRequest, VerifyRequest

OPERATIONS = {"generate": (GenerateRequest, core.generate), "verify": (VerifyRequest, core.verify),
              "revise": (ReviseRequest, core.revise)}


def main():
    parser = argparse.ArgumentParser(description="BioCheck单次运行（会真实调用模型）")
    parser.add_argument("operation", choices=OPERATIONS)
    parser.add_argument("request", help="与HTTP请求体相同的JSON文件")
    args = parser.parse_args()
    model, run = OPERATIONS[args.operation]
    request = model.model_validate_json(Path(args.request).read_text(encoding="utf-8"))
    try:
        result = run(request.model_dump())
    except core.InputError as error:
        print(json.dumps({"error_code": error.code, "message": error.message}, ensure_ascii=False))
        sys.exit(1)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
