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
pk-box is copied as-is to sources/ only: it contains intentionally illustrative
and sometimes invalid JSON templates. All other JSON errors fail normalization;
the existing normalized/ directory remains unchanged on failure.
"""
import json
import re
import shutil
import sys
import tempfile
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
            out.append(" ")
            while i < n and text[i] != "\n":
                i += 1
        elif c == "/" and i + 1 < n and text[i + 1] == "*":
            out.append(" ")
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            if i + 1 >= n:
                raise ValueError("unterminated block comment")
            i += 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def strip_trailing_commas(text: str) -> str:
    """Remove commas before } or ] only outside quoted strings."""
    out = []
    quoted = escaped = False
    for i, char in enumerate(text):
        if quoted:
            out.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
            out.append(char)
        elif char == ",":
            j = i + 1
            while j < len(text) and text[j] in " \t\r\n":
                j += 1
            if j == len(text) or text[j] not in "]}":
                out.append(char)
        else:
            out.append(char)
    return "".join(out)


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
    # Stage beside the destination. A failure cannot publish partial results.
    if not SRC.is_dir():
        print("sources/ missing; refusing to normalize", file=sys.stderr)
        return 1
    config = json.loads((ROOT / "sources.json").read_text(encoding="utf-8"))
    active = {item["name"] for item in config["sources"]}
    counts = {"json": 0, "copy": 0, "failed": 0, "raw_templates": 0}
    with tempfile.TemporaryDirectory(prefix=".normalize-", dir=ROOT) as tmp:
        stage = Path(tmp) / "normalized"
        if DST.exists():
            shutil.copytree(DST, stage)
        else:
            stage.mkdir()
        outputs = set()
        for src in sorted(SRC.rglob("*")):
            if not src.is_file():
                continue
            rel = src.relative_to(SRC)
            if rel.parts[0] not in active:
                continue  # Do not resurrect removed sources or delete legacy data.
            if rel.parts[0] == "pk-box":
                counts["raw_templates"] += 1
                continue  # Upstream examples may contain non-JSON placeholders.
            out_rel = rel.with_suffix(".json") if rel.suffix.lower() == ".jsonc" else rel
            if out_rel in outputs:
                counts["failed"] += 1
                print(f"  !! duplicate output: {out_rel}", file=sys.stderr)
                continue
            outputs.add(out_rel)
            try:
                kind = normalize_file(src, stage / rel)
                counts[kind] += 1
            except Exception as exc:  # noqa: BLE001
                counts["failed"] += 1
                print(f"  !! failed {rel}: {exc}", file=sys.stderr)
        if counts["failed"]:
            print(f"Normalization failed ({counts['failed']} files); prior normalized/ unchanged", file=sys.stderr)
            return 1
        # Remove stale paths for active, fully normalized sources; preserve legacy
        # and raw-template data instead of silently deleting it.
        for source in active - {"pk-box"}:
            directory = stage / source
            if directory.exists():
                for old in sorted(directory.rglob("*"), reverse=True):
                    if old.is_file() and old.relative_to(stage) not in outputs:
                        old.unlink()
                    elif old.is_dir() and not any(old.iterdir()):
                        old.rmdir()
        # Rename with rollback if replacement itself fails.
        backup = Path(tmp) / "previous"
        if DST.exists():
            DST.rename(backup)
        try:
            stage.rename(DST)
        except Exception:
            if backup.exists():
                backup.rename(DST)
            raise
    print(f"Normalized: {counts['json']} JSON, {counts['copy']} copied; "
          f"{counts['raw_templates']} pk-box raw template files kept in sources/ (not normalized)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
