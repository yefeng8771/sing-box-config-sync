#!/usr/bin/env python3
"""Normalize synced sources into normalized/.

Reads JSON/JSONC files under sources/, writes canonical JSON to normalized/
(sources/ is left untouched):

  - strip // and /* */ comments (JSONC -> JSON), respecting string literals
  - remove trailing commas
  - drop dict keys whose value is null
  - sort keys, indent=2, ensure_ascii=False

Non-JSON files (.srs, .md, ...) are copied through unchanged so normalized/
mirrors the sources/ tree.

Usage: python normalize.py
Exit 0 even if individual files fail (failures are reported, not fatal).
"""
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "sources"
DST = ROOT / "normalized"

JSON_EXTS = {".json", ".jsonc"}


def strip_comments(text: str) -> str:
    """Remove // and /* */ comments, respecting double-quoted strings."""
    out = []
    i, n = 0, len(text)
    in_str = False
    esc = False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            i += 1
        elif c == '"' :
            in_str = True
            out.append(c)
            i += 1
        elif c == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                i += 1
        elif c == "/" and i + 1 < n and text[i + 1] == "*":
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def strip_trailing_commas(text: str) -> str:
    return re.sub(r",(\s*[}\]])", r"\1", text)


def drop_nulls(obj):
    """Recursively drop dict keys with null values (lists keep nulls)."""
    if isinstance(obj, dict):
        return {k: drop_nulls(v) for k, v in obj.items() if v is not None}
    if isinstance(obj, list):
        return [drop_nulls(v) for v in obj]
    return obj


def normalize_file(src: Path, dst: Path) -> str:
    """Normalize one file. Returns 'json', 'copy', or raises."""
    if src.suffix.lower() in JSON_EXTS:
        text = src.read_text(encoding="utf-8")
        cleaned = strip_trailing_commas(strip_comments(text))
        obj = json.loads(cleaned)
        obj = drop_nulls(obj)
        # .jsonc -> .json (it is pure JSON now)
        if dst.suffix.lower() == ".jsonc":
            dst = dst.with_suffix(".json")
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(
            json.dumps(obj, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return "json"
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return "copy"


def main() -> int:
    if not SRC.exists():
        print("sources/ not found, nothing to normalize.")
        return 0
    counts = {"json": 0, "copy": 0, "failed": 0}
    for src in sorted(SRC.rglob("*")):
        if not src.is_file():
            continue
        rel = src.relative_to(SRC)
        dst = DST / rel
        try:
            kind = normalize_file(src, dst)
            counts[kind] += 1
        except Exception as exc:  # noqa: BLE001
            counts["failed"] += 1
            print(f"  !! skip {rel}: {exc}")
    print(f"Normalize done: {counts['json']} json, {counts['copy']} copied, "
          f"{counts['failed']} failed -> normalized/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
