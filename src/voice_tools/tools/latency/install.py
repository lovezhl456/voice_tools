"""Explicit installer shipped in wheels and source packages; never called by analyze."""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request

from voice_tools.core.files import read_json, sha256, write_json
from .runtime import DEFAULT_PREFIX, ROOT, RESOURCES, doctor, invoke, manifest


@contextmanager
def installation_lock(directory):
    directory.parent.mkdir(parents=True, exist_ok=True)
    lock = directory.with_name(directory.name + ".install.lock")
    try:
        fd = os.open(str(lock), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as error:
        raise ValueError(f"安装锁已存在：{lock}；确认没有安装进程后才能手动移除") from error
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(str(os.getpid()))
        yield
    finally:
        lock.unlink()


def extract_source(archive, target):
    # Accept only regular files/directories and strip the single archive root.
    with tarfile.open(archive, "r:gz") as source:
        members = source.getmembers()
        if sum(member.size for member in members) > 50 * 1024**2:
            raise ValueError("源码归档解码大小异常")
        for member in members:
            parts = Path(member.name).parts
            if len(parts) < 2:
                continue
            relative = Path(*parts[1:])
            if relative.is_absolute() or ".." in relative.parts or not (member.isfile() or member.isdir()):
                raise ValueError("源码归档含非预期路径或链接")
            dest = target / relative
            if member.isdir():
                dest.mkdir(parents=True, exist_ok=True)
            else:
                dest.parent.mkdir(parents=True, exist_ok=True)
                with source.extractfile(member) as src, dest.open("wb") as dst:
                    shutil.copyfileobj(src, dst)


def install(directory, python, archive=None):
    directory = Path(directory).expanduser().absolute()
    if directory.is_symlink() or directory.resolve() != directory:
        raise ValueError("请使用最终规范路径，安装前缀不可为符号链接")
    expected = manifest()
    probe = subprocess.check_output([str(python), "-I", "-c", "import sys; print('%d.%d' % sys.version_info[:2])"], text=True).strip()
    if probe not in ("3.11", "3.12"):
        raise ValueError("独立引擎要求 Python 3.11 或 3.12")
    with installation_lock(directory):
        if directory.exists():
            info = doctor(directory)
            if info["ready"]:
                return info
            raise ValueError(f"拒绝覆盖已有目录：{directory}；请选择新前缀。" + "; ".join(info["issues"]))
        with tempfile.TemporaryDirectory(prefix="latency-install-") as folder:
            source_archive = Path(folder) / "source.tar.gz"
            if archive:
                shutil.copyfile(archive, source_archive)
            else:
                url = f"https://codeload.github.com/signalwire/latency_checker/tar.gz/{expected['upstream_commit']}"
                with urllib.request.urlopen(url, timeout=60) as response, source_archive.open("wb") as output:
                    shutil.copyfileobj(response, output)
            if sha256(source_archive) != expected["archive_sha256"]:
                raise ValueError("上游归档 SHA-256 不匹配；未创建安装前缀")
            directory.mkdir()
            write_json(directory / "installing.json", {"manifest": expected, "prefix": str(directory)})
            extract_source(source_archive, directory / "source")
            target = directory / "source/src/latency_checker/detector.py"
            if sha256(target) != expected["original_detector_sha256"]:
                raise ValueError("原始 detector 校验失败")
            text = target.read_text()
            for edit in read_json(RESOURCES / "detector-edits.json"):
                if text.count(edit["before"]) != edit["count"]:
                    raise ValueError("补丁上下文不匹配")
                text = text.replace(edit["before"], edit["after"])
            target.write_text(text)
            if sha256(target) != expected["patched_detector_sha256"]:
                raise ValueError("补丁结果校验失败")
            shutil.copytree(RESOURCES, directory / "provenance")
            shutil.copyfile(ROOT / "worker.py", directory / "worker.py")
            subprocess.run([str(python), "-I", "-m", "venv", str(directory / "venv")], check=True)
            subprocess.run([str(directory / "venv/bin/python"), "-I", "-m", "pip", "install",
                            "--require-hashes", "--only-binary=:all:",
                            "-r", str(RESOURCES / "requirements.txt")], check=True)
            runtime = invoke(directory, ["--doctor"], 30)
            write_json(directory / "ready.tmp", {"manifest": expected, "prefix": str(directory), **runtime})
            os.replace(directory / "ready.tmp", directory / "ready.json")
            info = doctor(directory)
            if not info["ready"]:
                (directory / "ready.json").unlink()
                raise ValueError("安装验证未通过：" + "; ".join(info["issues"]))
            (directory / "installing.json").unlink()
            return info


def main():
    parser = argparse.ArgumentParser(description="显式安装固定版本 latency 引擎；升级使用新前缀")
    parser.add_argument("--prefix", type=Path, default=DEFAULT_PREFIX)
    parser.add_argument("--python", required=True, type=Path, help="Python 3.11/3.12 的解释器路径")
    parser.add_argument("--archive", type=Path, help="已下载的固定源码归档；依赖仍需完整可用的来源")
    args = parser.parse_args()
    try:
        print(json.dumps(install(args.prefix, args.python, args.archive), ensure_ascii=False))
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        parser.exit(2, f"安装失败：{error}\n")


if __name__ == "__main__":
    main()
