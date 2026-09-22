"""False-positive harness: run api_check_code over real, working source files.

Installed packages' own modules run correctly against their installed
dependencies, so every *error* the checker reports on them is a suspected
false positive. Test suites are skipped (they may exercise failure paths).

Usage:
    python scripts/check_corpus.py <python-exe> <site-packages> <pkg[,pkg...]> [--files N] [--db PATH]

Example:
    python scripts/check_corpus.py .venv/bin/python .venv/lib/python3.11/site-packages httpx,fastapi --files 60
"""
from __future__ import annotations

import argparse
import random
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from babels_hoard import checker, db, environments  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("python")
    parser.add_argument("site_packages")
    parser.add_argument("packages")
    parser.add_argument("--files", type=int, default=100)
    parser.add_argument("--db", default=None)
    parser.add_argument("--seed", type=int, default=1)
    args = parser.parse_args()

    db_path = Path(args.db) if args.db else Path(tempfile.mkdtemp()) / "corpus.db"
    conn = db.connect(db_path)
    env = environments.register_environment(conn, args.python)
    env.pop("probe", None)

    site = Path(args.site_packages)
    files: list[Path] = []
    for pkg in args.packages.split(","):
        files += [f for f in sorted((site / pkg).rglob("*.py")) if "tests" not in f.parts and "test" not in f.parts]
    random.seed(args.seed)
    files = random.sample(files, min(args.files, len(files)))

    severities: Counter[str] = Counter()
    checked = unchecked = 0
    started = time.time()
    for f in files:
        result = checker.api_check_code(conn, f.read_text(encoding="utf-8", errors="replace"), env_row=env)
        checked += result["checked"]
        unchecked += result["unchecked"]
        for finding in result["findings"]:
            severities[finding["severity"]] += 1
            print(f"{f.relative_to(site)}:{finding['line']} {finding['severity']} {finding['code']} {finding['message']}")
    print(
        f"{len(files)} files, {checked} verified, {unchecked} unchecked, "
        f"{severities['error']} errors, {severities['warning']} warnings, {time.time() - started:.0f}s"
    )
    return 1 if severities["error"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
