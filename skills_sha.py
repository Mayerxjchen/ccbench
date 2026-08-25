#!/usr/bin/env python3
"""计算 skill bundle 目录的确定性内容哈希(eval 与 build 共用)。

skills_sha 语义:对 ``skills/`` 目录下每个真实文件,按相对路径排序,
逐文件做 ``sha256(rel_path + '\\0' + file_sha256 + '\\0')``,再整体
sha256,返回完整 ``sha256:`` 前缀摘要。只计入普通文件,跳过以 ``.``
开头的条目(如 .DS_Store、.git)。

完整 sha256 与 release manifest / run-record 的摘要词汇一致(避免 16 位
截断与 ``sha256:`` 前缀并存时的漂移)。

用法:
    python3 skills_sha.py --dir <skills_dir>                    # 打印完整哈希
    python3 skills_sha.py --dir <skills_dir> --write <json> \
        --tag <tag> --commit <commit>                          # 写 .skill-image.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def hash_tree(root: Path) -> str:
    """确定性内容哈希:相对路径排序 + 文件内容,整体 sha256,``sha256:`` 前缀。"""
    entries: list[tuple[str, str]] = []
    for p in sorted(root.rglob("*")):
        if p.is_file() and not p.name.startswith("."):
            rel = p.relative_to(root).as_posix()
            entries.append((rel, file_sha256(p)))
    h = hashlib.sha256()
    for rel, digest in entries:
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        h.update(digest.encode("ascii"))
        h.update(b"\0")
    return "sha256:" + h.hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser(description="skill bundle 内容哈希")
    ap.add_argument("--dir", required=True, type=Path, help="skills 目录")
    ap.add_argument("--write", type=Path, default=None,
                    help="写入 .skill-image.json 的路径")
    ap.add_argument("--tag", default=None, help="镜像 immutable tag")
    ap.add_argument("--commit", default=None, help="skill 来源 git commit")
    args = ap.parse_args()

    digest = hash_tree(args.dir)
    if args.write:
        manifest = {
            "tag": args.tag,
            "commit": args.commit,
            "skills_sha": digest,
        }
        args.write.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(f"skills_sha={digest}  ->  {args.write}")
    else:
        print(digest)


if __name__ == "__main__":
    main()
