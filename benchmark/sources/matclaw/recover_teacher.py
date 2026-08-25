"""Recover the MatClaw teacher model from its author-published AIS Square archive."""

from __future__ import annotations

import argparse
import json
import shutil
import urllib.request
import zipfile
from pathlib import Path

from benchmark.sources.matclaw.recovery import sha256_file


AIS_RECORD = {
    "provider": "AIS Square",
    "record_id": 109,
    "record_name": "vdW_CuInP2S6_optB86b",
    "record_url": (
        "https://www.aissquare.com/models/detail?"
        "pageType=models&name=vdW_CuInP2S6_optB86b&id=109"
    ),
    "search_api_url": (
        "https://backend.aissquare.com/search?"
        "searchKey=vdW_CuInP2S6_optB86b&page=1&pageSize=10"
    ),
    "download_url": (
        "https://aisquare-zjk.oss-cn-zhangjiakou.aliyuncs.com/"
        "data/models/45119491-7e8d-4628-997a-bc596564bce4"
    ),
    "authors": ["Ri He", "Hua Wang", "Shi Liu", "Zhi cheng Zhong"],
    "version": "1",
    "published_at": "2023-07-08T00:00:00+08:00",
    "content_disposition_filename": "CIPS_data.zip",
    "reported_size": 39811218,
    "license": {
        "status": "not_exposed_by_public_search_api",
        "value": None,
    },
}
ARCHIVE_SHA256 = "1a8ebdd410fc6d6f417c5c0cd5a3fafce430530e57882bf9fcb03563886c42de"
MODEL_SHA256 = "a3e7cf9c8168c649ee1ba6e39a7212f3f9db29fc0162b093beb908927e956b4d"
MODEL_MEMBER = "CIPS_data/model/frozen_model/frozen_model.pb"
TYPE_MAP_MEMBER = "CIPS_data/dataset/init.000/type_map.raw"


def _artifact(root: Path, path: Path) -> dict[str, object]:
    return {
        "path": path.relative_to(root).as_posix(),
        "sha256": sha256_file(path),
        "size": path.stat().st_size,
    }


def download_archive(destination: Path) -> Path:
    """Download the pinned AIS Square archive and reject changed bytes."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".part")
    request = urllib.request.Request(
        AIS_RECORD["download_url"],
        headers={"User-Agent": "dftworld-source-recovery"},
    )
    with urllib.request.urlopen(request, timeout=180) as response:
        partial.write_bytes(response.read())
    if sha256_file(partial) != ARCHIVE_SHA256:
        partial.unlink()
        raise RuntimeError("AIS Square archive SHA-256 does not match pinned bytes")
    if partial.stat().st_size != AIS_RECORD["reported_size"]:
        partial.unlink()
        raise RuntimeError("AIS Square archive size does not match public metadata")
    with zipfile.ZipFile(partial) as bundle:
        if MODEL_MEMBER not in bundle.namelist() or TYPE_MAP_MEMBER not in bundle.namelist():
            partial.unlink()
            raise RuntimeError("AIS Square archive lacks pinned model members")
    partial.replace(destination)
    return destination


def prepare_teacher_files(root: Path, archive_source: Path) -> dict[str, Path]:
    """Copy the source archive and extract only the model/type-map evidence."""
    if sha256_file(archive_source) != ARCHIVE_SHA256:
        raise RuntimeError("AIS Square archive SHA-256 does not match pinned bytes")

    teacher = root / "common" / "teacher-model"
    teacher.mkdir(parents=True, exist_ok=True)
    archive = teacher / "CIPS_data.zip"
    shutil.copyfile(archive_source, archive)
    with zipfile.ZipFile(archive) as bundle:
        model_bytes = bundle.read(MODEL_MEMBER)
        type_map_bytes = bundle.read(TYPE_MAP_MEMBER)
    model = teacher / "frozen_model.pb"
    type_map_path = teacher / "type_map.raw"
    model.write_bytes(model_bytes)
    type_map_path.write_bytes(type_map_bytes)

    if sha256_file(model) != MODEL_SHA256:
        raise RuntimeError("extracted teacher model SHA-256 does not match pinned bytes")
    type_map = type_map_path.read_text(encoding="utf-8").split()
    if type_map != ["Cu", "In", "P", "S"]:
        raise RuntimeError(f"unexpected teacher-model type map: {type_map}")

    upstream_paper = (
        root
        / "repository"
        / "release"
        / "workspace_demo1b_distill_pdf"
        / "He_paper.pdf"
    )
    paper = root / "paper" / "he-physrevb-108-024305.pdf"
    paper.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(upstream_paper, paper)
    return {
        "archive": archive,
        "model": model,
        "type_map": type_map_path,
        "paper": paper,
    }


def validate_load_test_bindings(root: Path, load_test: dict) -> None:
    expected = {
        "model_sha256": sha256_file(root / "common" / "teacher-model" / "frozen_model.pb"),
        "structure_sha256": sha256_file(root / "common" / "CuInP2S6.cif"),
        "type_map_sha256": sha256_file(root / "common" / "teacher-model" / "type_map.raw"),
    }
    errors = [
        f"{key} does not bind to recovered bytes"
        for key, value in expected.items()
        if load_test.get(key) != value
    ]
    type_map = (root / "common" / "teacher-model" / "type_map.raw").read_text(
        encoding="utf-8"
    ).split()
    if load_test.get("model_type_map") != type_map:
        errors.append("embedded model type map does not match extracted type_map.raw")
    if load_test.get("type_map") != type_map:
        errors.append("load-test input type map does not match extracted type_map.raw")
    if load_test.get("passed") is not True:
        errors.append("load test did not pass")
    if load_test.get("outputs", {}).get("all_finite") is not True:
        errors.append("load-test outputs are not all finite")
    if errors:
        raise RuntimeError("invalid teacher load-test evidence: " + "; ".join(errors))


def recover_teacher(root: Path, archive_source: Path, load_test: dict) -> Path:
    prepared = prepare_teacher_files(root, archive_source)
    teacher = prepared["model"].parent
    archive = prepared["archive"]
    model = prepared["model"]
    type_map_path = prepared["type_map"]
    paper = prepared["paper"]
    type_map = type_map_path.read_text(encoding="utf-8").split()
    validate_load_test_bindings(root, load_test)

    recovery = {
        "schema_version": 1,
        "status": "recovered",
        "provenance": "upstream_author_dataset",
        "recovered_at": "2026-08-10",
        "paper": {
            "doi": "10.1103/PhysRevB.108.024305",
            "repository_source": (
                "repository/release/workspace_demo1b_distill_pdf/He_paper.pdf"
            ),
            "path": paper.relative_to(root).as_posix(),
            "sha256": sha256_file(paper),
            "size": paper.stat().st_size,
            "data_reference": 39,
        },
        "source": AIS_RECORD,
        "archive_member": MODEL_MEMBER,
        "type_map_member": TYPE_MAP_MEMBER,
        "type_map": type_map,
        "type_map_verified": True,
        "load_test": load_test,
        "artifacts": [
            _artifact(root, archive),
            _artifact(root, model),
            _artifact(root, type_map_path),
            _artifact(root, paper),
        ],
    }
    output = teacher / "recovery.json"
    output.write_text(
        json.dumps(recovery, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path)
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--load-test", type=Path)
    parser.add_argument("--download-archive", type=Path)
    parser.add_argument("--prepare", action="store_true")
    args = parser.parse_args()
    if args.download_archive:
        print(download_archive(args.download_archive.resolve()))
        return
    if args.root is None or args.archive is None:
        parser.error("--root and --archive are required unless downloading only")
    if args.prepare:
        for path in prepare_teacher_files(
            args.root.resolve(), args.archive.resolve()
        ).values():
            print(path)
        return
    if args.load_test is None:
        parser.error("--load-test is required for final recovery metadata")
    load_test = json.loads(args.load_test.read_text(encoding="utf-8"))
    print(recover_teacher(args.root.resolve(), args.archive.resolve(), load_test))


if __name__ == "__main__":
    main()
