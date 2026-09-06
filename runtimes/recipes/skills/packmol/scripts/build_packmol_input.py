#!/usr/bin/env python3
"""Generate a Packmol input from a validated, normalized manifest."""

from __future__ import annotations

import argparse
from pathlib import Path

from packmol_common import emit_result, fail, load_json


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest")
    args = parser.parse_args()
    source = Path(args.manifest)
    try:
        manifest = load_json(source)
        derived = manifest["derived"]
        packmol = manifest["packmol"]
        dimensions = [float(value) for value in derived["box_dimensions_A"]]
        target = Path(packmol["input_path"])
        target.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            f"tolerance {float(packmol['tolerance_A']):g}",
            "filetype xyz",
            f"output {packmol['output_path']}",
        ]
        if packmol.get("seed") is not None:
            lines.append(f"seed {packmol['seed']}")
        if manifest["box"]["periodic"]:
            lines.append(
                "pbc 0.0 0.0 0.0 " + " ".join(str(value) for value in dimensions)
            )
        for component in manifest["components"]:
            lines.extend(
                [
                    "",
                    f"structure {component['template_path']}",
                    f"  number {component['count']}",
                    "  inside box 0.0 0.0 0.0 "
                    + " ".join(str(value) for value in dimensions),
                    "end structure",
                ]
            )
        target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except Exception as exc:
        return fail(
            check="artifact",
            name="packmol_input_generated",
            error=exc,
            source_paths=[str(source)],
        )
    emit_result(
        check="artifact",
        name="packmol_input_generated",
        status="PASS",
        preparation_stage="built",
        source_paths=[str(source), str(target)],
        input_path=str(target),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
