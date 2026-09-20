"""Explicit, bounded downloads; inference only reads a verified local checkpoint."""
import hashlib
import os
from pathlib import Path
import tempfile
import urllib.error
import urllib.request

COMMIT = "fe84f0f252abec382b24367d5b22498a7ce34dbb"
FILENAME = "nisqa.tar"
URL = f"https://raw.githubusercontent.com/gabrielmittag/NISQA/{COMMIT}/weights/{FILENAME}"
SHA256 = "7ec4cf937514dd3f8860b21e66fabd8ca87a168572675ef8d979c4c4ad2e805c"
SIZE = 1051663
LICENSE = "CC-BY-NC-SA-4.0"
LICENSE_URL = f"https://github.com/gabrielmittag/NISQA/blob/{COMMIT}/weights/LICENSE_model_weights"


def default_model_dir():
    return Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))) / "voice-tools" / "nisqa"


def model_path(directory=None):
    return (Path(directory) if directory is not None else default_model_dir()).expanduser().resolve() / FILENAME


def read_verified(directory=None):
    path = model_path(directory)
    if path.is_symlink():
        raise ValueError(f"权重不能是符号链接：{path}")
    if not path.is_file():
        raise ValueError(f"缺少本地权重：{path}；先运行 voice-tools nisqa download（可指定相同 --model-dir）")
    with path.open("rb") as stream:
        data = stream.read(SIZE + 1)
    if len(data) != SIZE or hashlib.sha256(data).hexdigest() != SHA256:
        raise ValueError(f"权重大小或 SHA-256 不匹配：{path}；可用 nisqa download --force 重新下载")
    return data


def describe(directory=None):
    path = model_path(directory)
    result = {"path": str(path), "url": URL, "commit": COMMIT, "sha256": SHA256,
              "bytes": SIZE, "license": LICENSE, "license_url": LICENSE_URL}
    try:
        read_verified(directory)
        result["valid"] = True
    except (ValueError, OSError) as error:
        result.update(valid=False, error=str(error))
    return result


def download(directory=None, force=False):
    path = model_path(directory)
    if path.is_symlink():
        raise ValueError(f"拒绝替换符号链接：{path}")
    if path.exists() and not force:
        read_verified(directory)
        return {**describe(directory), "downloaded": False}
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        request = urllib.request.Request(URL, headers={"User-Agent": "voice-tools-nisqa"})
        with urllib.request.urlopen(request, timeout=60) as response:
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".nisqa-", suffix=".part", delete=False) as stream:
                temporary = Path(stream.name)
                size = 0
                digest = hashlib.sha256()
                while True:
                    block = response.read(64 * 1024)
                    if not block:
                        break
                    size += len(block)
                    if size > SIZE:
                        raise ValueError("权重下载超过固定文件大小；拒绝保存")
                    digest.update(block)
                    stream.write(block)
            if size != SIZE or digest.hexdigest() != SHA256:
                raise ValueError("下载文件大小或 SHA-256 不符；未替换原权重")
        os.replace(temporary, path)
        return {**describe(directory), "downloaded": True}
    except urllib.error.URLError as error:
        raise ValueError(f"权重下载失败：{error.reason}；原文件未替换") from error
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
