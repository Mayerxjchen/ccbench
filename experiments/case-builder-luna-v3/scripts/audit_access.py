#!/usr/bin/env python3
"""File-access audit over a driver transcript (JSONL).

Eval-side integrity check: proves which tool calls in the driver's
transcript touched the planted answer files. Emits compact verdict lines
only — the transcript itself is never printed.

Usage:
    python audit_access.py TRANSCRIPT.jsonl --target PATTERN \
        [--content-token TOKEN]...

`--target` is a substring matched against the serialized tool-call INPUT
(file_path, command, pattern, path, ...) — it detects attempts to open the
planted paths. `--content-token` is a unique string from the planted file
BODIES matched against the RAW transcript line — a hit means the content
entered the transcript, most commonly via a tool RESULT, which is the
decisive non-access evidence.

Exit code 0 = zero input hits and zero token hits, 1 = hits (listed),
2 = usage/parse error.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def iter_tool_uses(rec):
    msg = rec.get("message") or {}
    content = msg.get("content")
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and item.get("type") == "tool_use":
                yield item


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("transcript", type=Path)
    ap.add_argument("--target", action="append", required=True)
    ap.add_argument("--content-token", action="append", default=[])
    args = ap.parse_args()

    total_calls = 0
    hits = []
    token_hits = {tok: 0 for tok in args.content_token}
    unparsed = 0
    with args.transcript.open() as fh:
        for line in fh:
            for tok in args.content_token:
                if tok in line:
                    token_hits[tok] += 1
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                unparsed += 1
                continue
            for call in iter_tool_uses(rec):
                total_calls += 1
                name = call.get("name", "?")
                inp = call.get("input") or {}
                blob = json.dumps(inp, ensure_ascii=False)
                for pat in args.target:
                    if pat in blob:
                        snippet = blob if len(blob) <= 200 else blob[:200] + "…"
                        hits.append((name, pat, snippet))
                        break

    print(f"tool_calls_seen={total_calls} unparsed_records={unparsed}")
    print(f"targets={args.target}")
    bad = bool(hits)
    for tok, count in token_hits.items():
        status = "CLEAN" if count == 0 else "LEAKED"
        print(f"content_token '{tok}': {status} ({count} transcript lines)")
        bad = bad or count > 0
    if hits:
        print(f"ACCESSES={len(hits)}")
        for name, pat, snippet in hits:
            print(f"  [{name}] matched '{pat}': {snippet}")
        return 1
    print("ACCESSES=0" + ("" if not token_hits else " (input paths); "
          + "tokens clean" if not bad else "but token leak — see above"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
