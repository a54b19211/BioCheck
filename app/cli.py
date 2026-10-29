"""python3 -m app.cli CASE.json --output runs/example --method bound"""
import argparse
import json
from pathlib import Path

from .core import run, verify
from .model import DeepSeek


def main():
    parser = argparse.ArgumentParser(description="BioCheck：真实材料上的生成、核验与有限修订")
    parser.add_argument("input", help="question/evidence组成的JSON")
    parser.add_argument("--output", required=True, help="本次输出目录")
    parser.add_argument("--method", choices=["direct", "plain", "bound"], default="bound")
    parser.add_argument("--check", help="仅核验这份UTF-8文本")
    parser.add_argument("--no-repair", action="store_true")
    parser.add_argument("--model")
    parser.add_argument("--max-calls", type=int)
    args = parser.parse_args()
    output = Path(args.output)
    if (output / "result.json").exists() or (output / "calls").exists():
        parser.error("输出目录已有运行记录，请选新目录")
    output.mkdir(parents=True, exist_ok=True)
    packet = json.loads(Path(args.input).read_text())
    model = DeepSeek(output / "calls", model=args.model, max_calls=args.max_calls)
    result = (verify(packet, Path(args.check).read_text(), model) if args.check
              else run(packet, model, args.method, repair=not args.no_repair))
    (output / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    text = result.get("final_text", result.get("draft", ""))
    (output / "answer.txt").write_text(text)
    print(json.dumps({"status": result["status"], "calls": len(model.calls),
                      "output": str(output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
