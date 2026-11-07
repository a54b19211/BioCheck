"""Function size and cyclomatic complexity of the Python sources, with a CI guard.

    python <dir>/code_metrics.py [paths...]          # report: file sizes, the longest, the most complex
    python <dir>/code_metrics.py --check [paths...]  # fail on a function over the limits without a reason

Without paths it measures those of `[tool.code-metrics] paths` in the repository's pyproject.toml (the
directory above this script). The script is the same file in every repository (devtools keeps the canonical
copy and checks the copies); only that setting and the exceptions file are the repository's own.

A function's length is its lines from `def` to its last line. Its complexity is McCabe's: 1 plus one per
if / elif / for / while / except / with-less branch point, conditional expression, comprehension `for` and
`if`, `case`, and each extra operand of `and` / `or`. Nested functions and lambdas count towards the function
that holds them (moving a branch into a closure does not make the function simpler) and are also listed on
their own.

The limits are 80 lines and complexity 20. The functions that were already over them when this gate was
introduced are frozen in metrics_exceptions.txt next to this script as
`path:qualified.name  <lines>/<cc>  reason`, where <lines>/<cc> is the size they had then. --check fails on

  - a function over the limits that is not listed (a new violator),
  - a listed function that is within the limits now (remove the entry: the list only shrinks),
  - a listed function that grew past its frozen size or complexity (a frozen violator may not get worse).

When a listed function is made smaller but is still over a limit, lower its numbers in the entry."""

import argparse
import ast
import pathlib
import sys

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10: tomli, the same parser (a pinned dependency of pytest there)
    import tomli as tomllib

MAX_LINES, MAX_CC = 80, 20
ROOT = pathlib.Path(__file__).resolve().parent.parent
EXCEPTIONS = pathlib.Path(__file__).resolve().parent / "metrics_exceptions.txt"


def configured_paths():
    """The measured paths: `[tool.code-metrics] paths` in pyproject.toml (tests are usually not measured)."""
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    paths = config.get("tool", {}).get("code-metrics", {}).get("paths")
    if not paths or not all(isinstance(p, str) for p in paths):
        sys.exit(f"{ROOT / 'pyproject.toml'}: [tool.code-metrics] paths must be a non-empty list of paths")
    return tuple(paths)


DEFAULT_PATHS = configured_paths()


def complexity(node):
    cc = 1
    for n in ast.walk(node):
        if isinstance(n, (ast.If, ast.For, ast.AsyncFor, ast.While, ast.IfExp, ast.ExceptHandler)):
            cc += 1
        elif isinstance(n, ast.BoolOp):
            cc += len(n.values) - 1
        elif isinstance(n, ast.comprehension):
            cc += 1 + len(n.ifs)
        elif isinstance(n, ast.match_case):
            cc += 1
    return cc


def functions(path):
    tree = ast.parse(path.read_text(encoding="utf-8"), str(path))

    def walk(node, prefix):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = f"{prefix}{child.name}"
                yield name, child.lineno, child.end_lineno - child.lineno + 1, complexity(child)
                yield from walk(child, name + ".")
            elif isinstance(child, ast.ClassDef):
                yield from walk(child, f"{prefix}{child.name}.")
            else:
                yield from walk(child, prefix)

    yield from walk(tree, "")


def collect(paths):
    files, rows = {}, []
    for base in paths:
        base = pathlib.Path(base)
        if not base.exists():
            continue
        for path in sorted(base.rglob("*.py") if base.is_dir() else [base]):
            if ".venv" in path.parts or "__pycache__" in path.parts:
                continue
            rel = path.resolve().relative_to(ROOT).as_posix()
            files[rel] = len(path.read_text(encoding="utf-8").splitlines())
            rows += [(rel, name, line, size, cc) for name, line, size, cc in functions(path)]
    return files, rows


def exceptions():
    """{path:name: (lines, cc, reason)} from the exceptions file."""
    out = {}
    if EXCEPTIONS.exists():
        for line in EXCEPTIONS.read_text(encoding="utf-8").splitlines():
            if line.strip() and not line.startswith("#"):
                key, size, reason = line.split(None, 2)
                lines, _, cc = size.partition("/")
                out[key] = (int(lines), int(cc), reason.strip())
    return out


def report(files, rows, top):
    print("largest files:")
    for rel, n in sorted(files.items(), key=lambda x: -x[1])[:top]:
        print(f"  {n:6d}  {rel}")
    print("longest functions:")
    for rel, name, line, size, cc in sorted(rows, key=lambda r: -r[3])[:top]:
        print(f"  {size:6d} lines  cc {cc:3d}  {rel}:{line} {name}")
    print("most complex functions:")
    for rel, name, line, size, cc in sorted(rows, key=lambda r: -r[4])[:top]:
        print(f"  cc {cc:3d}  {size:6d} lines  {rel}:{line} {name}")
    over = [r for r in rows if r[3] > MAX_LINES or r[4] > MAX_CC]
    print(f"{len(rows)} functions; {len(over)} over {MAX_LINES} lines or complexity {MAX_CC}")


def problems_of(rows, allowed):
    """The list of reasons the check fails; empty when the gate holds."""
    problems = []
    seen = {}
    for rel, name, _, size, cc in rows:
        key = f"{rel}:{name}"
        # a name used twice in one file (e.g. a property and its setter) is measured as the larger of them
        old = seen.get(key, (0, 0))
        seen[key] = (max(size, old[0]), max(cc, old[1]))
    over = {k: v for k, v in seen.items() if v[0] > MAX_LINES or v[1] > MAX_CC}
    for key, (size, cc) in sorted(over.items()):
        if key not in allowed:
            problems.append(
                f"{key}: {size} lines, complexity {cc} (limits {MAX_LINES} / {MAX_CC}), no reason"
            )
            continue
        frozen_lines, frozen_cc, _ = allowed[key]
        if size > frozen_lines or cc > frozen_cc:
            problems.append(
                f"{key}: grew to {size} lines, complexity {cc} (frozen at {frozen_lines}/{frozen_cc})"
            )
    for key in sorted(set(allowed) - set(over)):
        problems.append(
            f"{key}: listed in {EXCEPTIONS.name} but gone or within the limits now - remove the entry"
        )
    return problems


def check(rows):
    problems = problems_of(rows, exceptions())
    for p in problems:
        print(p)
    return 1 if problems else 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="*", default=[ROOT / p for p in DEFAULT_PATHS])
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--top", type=int, default=10)
    parser.add_argument(
        "--freeze",
        action="store_true",
        help="print every function over the limits as exception-file lines (a starting point for reasons)",
    )
    args = parser.parse_args()
    files, rows = collect(args.paths)
    if args.freeze:
        for rel, name, _, size, cc in sorted(rows):
            if size > MAX_LINES or cc > MAX_CC:
                print(f"{rel}:{name}  {size}/{cc}  REASON")
        return
    if args.check:
        sys.exit(check(rows))
    report(files, rows, args.top)


if __name__ == "__main__":
    main()
