"""Offline demonstrator: python -m app.cli examples/good.json"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from .core import process


def main() -> None:
    parser = argparse.ArgumentParser(description='固定证据问答与规则核验参考原型')
    parser.add_argument('input', help='JSON 固定证据包路径')
    parser.add_argument('--output', help='保存结果路径（可选）')
    parser.add_argument('--generate', action='store_true', help='忽略输入答案，按模板生成回答')
    args = parser.parse_args()
    data = json.loads(Path(args.input).read_text(encoding='utf-8'))
    result = process(data, use_supplied_answer=not args.generate)
    output = json.dumps(result, ensure_ascii=False, indent=2)
    print(output)
    if args.output:
        Path(args.output).write_text(output, encoding='utf-8')


if __name__ == '__main__':
    main()
