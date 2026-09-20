#!/usr/bin/env python3
"""将已安装的 ViSQOL 导出为普通文件，用于同平台 Docker 运行镜像。"""
import argparse
from pathlib import Path
import shutil
import sys

from voice_tools.core.files import new_output, sha256, write_json
from voice_tools.tools.visqol.service import require_backend


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--visqol-dir", type=Path)
    parser.add_argument("--out", type=Path, required=True, help="新建或空的导出目录")
    args = parser.parse_args(argv)
    speech = require_backend(args.visqol_dir, "speech")
    audio = require_backend(args.visqol_dir, "audio")
    out = new_output(args.out.expanduser().resolve())
    source = out / "source"
    (source / "bazel-bin").mkdir(parents=True)
    (source / "model").mkdir()
    binary = source / "bazel-bin/visqol"
    shutil.copyfile(speech["binary"], binary)
    binary.chmod(0o755)
    if sha256(binary) != speech["binary_sha256"]:
        raise ValueError("导出过程中的二进制校验不符")
    models = {}
    for info in (speech, audio):
        model = Path(info["model"])
        target = source / "model" / model.name
        shutil.copyfile(model, target)
        if sha256(target) != info["model_sha256"]:
            raise ValueError("导出过程中的模型校验不符")
        models[info["mode"]] = {"file": model.name, "sha256": info["model_sha256"]}
    for name in ("LICENSE", "NOTICE"):
        original = Path(speech["directory"]) / name
        if original.is_file():
            shutil.copyfile(original, source / name)
    write_json(out / "installation.json", {
        "schema_version": "1.0", "status": "built", "package_kind": "runtime_export",
        "binary_sha256": speech["binary_sha256"], "models": models,
        "source_installation": speech["installation"],
        "scope": "同平台运行文件；未携带 Bazel 缓存或演示音频，动态库兼容性需在目标镜像实际评分验证。"})
    print(f"已导出：{out}；下一步构建运行镜像并实际评分。")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError) as error:
        print(f"导出失败：{error}", file=sys.stderr)
        raise SystemExit(2)
