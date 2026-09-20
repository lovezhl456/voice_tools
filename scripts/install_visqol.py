#!/usr/bin/env python3
"""固定版本 CPU ViSQOL 安装器；仅修改指定前缀和缓存，失败时可原命令重试。"""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import signal
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time

COMMIT = "38d0b0163e441047d4429bf07ad09e5b9031d02c"
SOURCE_SHA256 = "dbfe6283f362560cedee7441255c1d95ed31e977ecc2854fc60db020c788a508"
BAZEL_VERSION = "5.1.0"
TFRT_COMMIT = "4ce3e4da2e21ae4dfcee9366415e55f408c884ec"
TFRT_SHA256 = "a0aa5ab0af90684db7a1cf8dd3d21081e44ef54eb9a1b6b9bb8b051d3ed25a67"


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for part in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(part)
    return h.hexdigest()


def download(url, destination, expected=None):
    destination = Path(destination)
    if destination.is_symlink():
        raise ValueError(f"下载目标不可为符号链接：{destination}")
    if destination.exists() and expected and digest(destination) == expected:
        return destination
    if destination.exists():
        raise ValueError(f"已有缓存未通过预期校验，不覆盖：{destination}；请更换缓存目录或手工核对该文件")
    destination.parent.mkdir(parents=True, exist_ok=True)
    part = destination.with_name(destination.name + ".part")
    if part.is_symlink():
        raise ValueError(f"临时下载目标不可为符号链接：{part}")
    subprocess.run(["curl", "--fail", "--location", "--retry", "3", "--connect-timeout", "20",
                    "--max-time", "900", "--output", str(part), url], check=True)
    if expected and digest(part) != expected:
        raise ValueError(f"SHA256 不符，未安装：{part}")
    part.replace(destination)
    return destination


def extract_verified(archive, source):
    """只提取固定官方归档的普通文件，拒绝越界、链接和覆盖用户改动。"""
    source = Path(source)
    if digest(archive) != SOURCE_SHA256:
        raise ValueError("ViSQOL 源码归档 SHA256 不符")
    if source.is_symlink():
        raise ValueError("源码目录不可为符号链接")
    source.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:gz") as bundle:
        planned = []
        names = set()
        for item in bundle.getmembers():
            parts = Path(item.name).parts
            if not parts or parts[0] != "visqol-" + COMMIT or ".." in parts or Path(item.name).is_absolute():
                raise ValueError("源码归档包含非法路径")
            if len(parts) == 1:
                continue
            if not (item.isfile() or item.isdir()):
                raise ValueError("源码归档包含符号链接或非常规文件")
            relative = Path(*parts[1:]); target = source / relative
            if target.is_symlink() or any(p.is_symlink() for p in target.parents if p != source.parent):
                raise ValueError(f"提取目标含链接：{target}")
            if item.isfile():
                with bundle.extractfile(item) as stream:
                    data = stream.read()
                if target.exists() and (not target.is_file() or digest(target) != hashlib.sha256(data).hexdigest()):
                    raise ValueError(f"现有源码与固定版本不同，不覆盖：{target}")
                planned.append((target, data, item.mode & 0o777))
                names.add(relative.as_posix())
        for path in source.rglob("*"):
            if path.is_dir() and not path.is_symlink():
                continue
            relative = path.relative_to(source)
            if len(relative.parts) == 1 and path.is_symlink() and relative.name in {"bazel-bin", "bazel-out", "bazel-testlogs", "bazel-source"}:
                continue
            if relative.as_posix() not in names:
                raise ValueError(f"源码目录有非归档文件，不覆盖或编译未知修改：{relative}")
        for target, data, mode in planned:
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data); target.chmod(mode)




TF_ELEMENTWISE_SHA256 = "2c3fd99764287694f2f052fac06f78221e75beca3a9d2f120927c36ef3338771"
TF_ABS_OLD = b"return EvalImpl<float>(context, node, std::abs<float>, type);"
TF_ABS_NEW = b"return EvalImpl<float>(context, node, [](float value) { return std::abs(value); }, type);"


def patch_tflite_for_mac(prefix):
    """只修补本前缀中固定 SHA 的 TF2.11 文件；未知内容一律拒绝。"""
    matches = list((Path(prefix) / "bazel-cache").glob("*/external/org_tensorflow/tensorflow/lite/kernels/elementwise.cc"))
    if not matches:
        return None
    if len(matches) != 1:
        raise ValueError("前缀中发现多个 TensorFlow 缓存，无法安全确定兼容补丁目标")
    path = matches[0]
    if path.is_symlink() or not path.resolve().is_relative_to(Path(prefix).resolve()):
        raise ValueError("TensorFlow 补丁目标越出本安装前缀")
    data = path.read_bytes()
    if TF_ABS_NEW in data:
        original = data.replace(TF_ABS_NEW, TF_ABS_OLD)
        if hashlib.sha256(original).hexdigest() != TF_ELEMENTWISE_SHA256:
            raise ValueError("TensorFlow 文件包含未知修改，不继续构建")
    else:
        if hashlib.sha256(data).hexdigest() != TF_ELEMENTWISE_SHA256 or data.count(TF_ABS_OLD) != 1:
            raise ValueError("TensorFlow 文件不是已核对的固定版本，不应用兼容补丁")
        data = data.replace(TF_ABS_OLD, TF_ABS_NEW)
        temporary = path.with_name(path.name + ".voice-tools-tmp")
        if temporary.exists() or temporary.is_symlink():
            raise ValueError("补丁临时文件已存在，请核对后再重试")
        temporary.write_bytes(data)
        temporary.replace(path)
    return {"dependency": "TensorFlow 2.11.0 d5b57ca93e506df258271ea00fc29cf98383a374",
            "file": "tensorflow/lite/kernels/elementwise.cc", "original_sha256": TF_ELEMENTWISE_SHA256,
            "patched_sha256": digest(path), "change": "std::abs<float> 改为调用 std::abs(float) 的等价 lambda，兼容现代 libc++"}


def run_build(argv, source, env, stream, timeout):
    process = subprocess.Popen(argv, cwd=source, env=env, stdout=stream, stderr=subprocess.STDOUT,
                               start_new_session=os.name == "posix")
    try:
        code = process.wait(timeout=timeout)
    except BaseException:
        if os.name == "posix":
            try:os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:pass
        else:process.kill()
        process.wait()
        raise
    if code:
        raise subprocess.CalledProcessError(code, argv)


LINUX_BAZEL_SHA256 = "0440ae4581ea5eac5cb36ed0790b1e942778eb81e3ba9bc1326f189427aef0fd"
QEMU_PROC_SOURCE = r"""#define _GNU_SOURCE
#include <dlfcn.h>
#include <fcntl.h>
#include <stdarg.h>
#include <stdlib.h>
#include <string.h>
#include <sys/types.h>
static char *self_exe;
__attribute__((constructor)) static void initialize(void) {
    const char *value = getenv("VISQOL_BAZEL_SELF_EXE");
    if (value) self_exe = strdup(value);
    unsetenv("LD_PRELOAD");
    unsetenv("VISQOL_BAZEL_SELF_EXE");
}
static const char *rewrite(const char *name) {
    return self_exe && strcmp(name, "/proc/self/exe") == 0 ? self_exe : name;
}
#define DEFINE_OPEN(symbol) \
int symbol(const char *name, int flags, ...) { \
    mode_t mode = 0; \
    if ((flags & O_CREAT) || (flags & O_TMPFILE) == O_TMPFILE) { \
        va_list args; va_start(args, flags); mode = va_arg(args, mode_t); va_end(args); \
    } \
    int (*original)(const char *, int, ...) = dlsym(RTLD_NEXT, #symbol); \
    return original(rewrite(name), flags, mode); \
}
DEFINE_OPEN(open)
DEFINE_OPEN(open64)
"""


def qemu_bazel_launcher(bazel, prefix):
    """仅对固定官方 Linux launcher 注入文件打开兼容层；不修改官方二进制。"""
    if digest(bazel) != LINUX_BAZEL_SHA256:
        raise ValueError("QEMU 兼容层只接受已核对的官方 Bazel 5.1.0 Linux x86_64 文件")
    if not shutil.which("gcc"):
        raise ValueError("QEMU 兼容层需要 gcc；请使用提供的 Docker 工具镜像")
    directory = Path(tempfile.mkdtemp(prefix="qemu-compat-", dir=prefix))
    source = directory / "proc_self.c"
    library = directory / "proc_self.so"
    launcher = directory / "bazel"
    source.write_text(QEMU_PROC_SOURCE)
    subprocess.run(["gcc", "-shared", "-fPIC", "-Wall", "-Wextra", str(source), "-ldl", "-o", str(library)],
                   check=True, timeout=120)
    launcher.write_text("#!/bin/sh\nexec env VISQOL_BAZEL_SELF_EXE=" + shlex.quote(str(bazel)) +
                        " LD_PRELOAD=" + shlex.quote(str(library)) + " " + shlex.quote(str(bazel)) + ' "$@"\n')
    launcher.chmod(0o700)
    return launcher, {"reason": "旧 Docker/QEMU 无法 seek /proc/self/exe",
                      "original_binary": str(bazel), "original_sha256": digest(bazel),
                      "launcher": str(launcher), "launcher_sha256": digest(launcher),
                      "source_sha256": digest(source), "library_sha256": digest(library),
                      "scope": "仅重定向 Bazel launcher 的 /proc/self/exe 打开；构造函数清除注入环境，不传给 Java 或编译器"}


def check_bazel(bazel, prefix, allow_qemu=False):
    result = subprocess.run([str(bazel), "--version"], capture_output=True, text=True, timeout=60)
    compatibility = None
    if result.returncode:
        if (allow_qemu and platform.system() == "Linux" and
                "Failed to open '/proc/self/exe' as a zip file" in result.stderr):
            bazel, compatibility = qemu_bazel_launcher(bazel, prefix)
            result = subprocess.run([str(bazel), "--version"], capture_output=True, text=True, timeout=60)
        if result.returncode:
            raise ValueError(f"Bazel 无法启动（退出 {result.returncode}）：{result.stderr.strip()}")
    version = result.stdout.strip()
    if version != "bazel " + BAZEL_VERSION:
        raise ValueError(f"此固定安装路线要求 Bazel {BAZEL_VERSION}，收到 {version}")
    return bazel, version, compatibility


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", type=Path, default=Path.home() / ".local/share/voice-tools/visqol")
    parser.add_argument("--download-cache", type=Path, help="复用已校验下载；默认 PREFIX/downloads")
    parser.add_argument("--bazel", type=Path, help="使用已有 Bazel 5.1.0 可执行文件；默认下载官方版本")
    parser.add_argument("--jobs", type=int, default=2, help="Bazel 并行编译任务数，默认 2")
    parser.add_argument("--memory-mb", type=int, default=4096, help="Bazel 调度内存预算，不是系统硬内存上限")
    parser.add_argument("--build-timeout", type=int, default=3600, help="构建最长秒数，默认 3600")
    parser.add_argument("--qemu-proc-self-workaround", action="store_true", help="仅在旧 Docker/QEMU 的 Bazel /proc/self/exe 错误出现时启用已核对的启动兼容层")
    args = parser.parse_args(argv)
    if args.jobs < 1 or args.memory_mb < 512 or args.build_timeout < 1:
        raise ValueError("jobs/timeout 须大于 0，memory-mb 须至少 512")
    system = platform.system(); machine = platform.machine().lower()
    arch = "arm64" if machine in ("arm64", "aarch64") else "x86_64" if machine in ("x86_64", "amd64") else machine
    if (system, arch) not in {("Darwin", "arm64"), ("Darwin", "x86_64"), ("Linux", "x86_64")}:
        raise ValueError("安装脚本目前仅提供 macOS arm64/x86_64 与 Linux x86_64 路线；其他平台请按上游手动构建")
    if not (3, 9) <= sys.version_info[:2] < (3, 12):
        raise ValueError("本安装路线使用 Python 3.9–3.11，以兼容固定版本的旧构建依赖")
    for name in ("git", "curl"):
        if not shutil.which(name):
            raise ValueError(f"缺少 {name}，请先安装系统构建前置工具")
    if importlib.util.find_spec("numpy") is None:
        raise ValueError("当前 Python 缺少 NumPy；请在 voice_tools 虚拟环境内运行本脚本")
    prefix = args.prefix.expanduser().resolve()
    prefix.mkdir(parents=True, exist_ok=True)
    previous = prefix / "installation.json"
    if previous.exists() and json.loads(previous.read_text()).get("source_commit") != COMMIT:
        raise ValueError("该前缀已有其他安装记录，请更换前缀")
    cache = (args.download_cache or prefix / "downloads").expanduser().resolve()
    cache.mkdir(parents=True, exist_ok=True)
    archive = download(f"https://codeload.github.com/google/visqol/tar.gz/{COMMIT}", cache / "visqol.tar.gz", SOURCE_SHA256)
    source = prefix / "source"
    extract_verified(archive, source)
    if args.bazel:
        bazel = args.bazel.expanduser().resolve()
    else:
        name = f"bazel-{BAZEL_VERSION}-{'darwin' if system == 'Darwin' else 'linux'}-{arch}"
        release = f"https://github.com/bazelbuild/bazel/releases/download/{BAZEL_VERSION}/{name}"
        if system == "Darwin" and arch == "arm64":
            expected = "485afe1117d129c9a792ef484a7108e053e99ddb239591f3b8469091dd8359c2"
        elif system == "Linux" and arch == "x86_64":
            expected = LINUX_BAZEL_SHA256
        else:
            checksum = cache / (name + ".sha256")
            if not checksum.exists():download(release + ".sha256", checksum)
            expected = checksum.read_text().split()[0]
            if len(expected) != 64 or any(c not in "0123456789abcdef" for c in expected):
                raise ValueError("官方 Bazel 校验文件格式无效")
        cached = download(release, cache / name, expected)
        bazel = prefix / "bin/bazel"
        bazel.parent.mkdir(exist_ok=True)
        if bazel.is_symlink() or (bazel.exists() and digest(bazel) != expected):
            raise ValueError("目标 Bazel 已存在且内容不同，不覆盖")
        if not bazel.exists():shutil.copyfile(cached, bazel)
        bazel.chmod(0o755)
    # 固定版本的部分镜像失效或 Java 下载器被限流；curl 预取同一官方归档。
    archives = [
        (f"https://codeload.github.com/tensorflow/runtime/tar.gz/{TFRT_COMMIT}", TFRT_COMMIT + ".tar.gz", TFRT_SHA256),
        ("https://codeload.github.com/protocolbuffers/protobuf/tar.gz/v3.19.1", "v3.19.1.tar.gz", "87407cd28e7a9c95d9f61a098a53cf031109d451a7763e7dd1253abf8b4df422"),
        ("https://codeload.github.com/bazelbuild/rules_java/tar.gz/981f06c3d2bd10225e85209904090eb7b5fb26bd", "981f06c3d2bd10225e85209904090eb7b5fb26bd.tar.gz", "f5a3e477e579231fca27bf202bb0e8fbe4fc6339d63b38ccb87c2760b533d1c3"),
        ("https://codeload.github.com/bazelbuild/rules_proto/zip/f7a30f6f80006b591fa7c437fe5a951eb10bcbcf", "f7a30f6f80006b591fa7c437fe5a951eb10bcbcf.zip", "a4382f78723af788f0bc19fd4c8411f44ffe0a72723670a34692ffad56ada3ac"),
        ("https://codeload.github.com/cjlin1/libsvm/zip/v324", "v324.zip", "401a60bd828bce8870b9eebf5023602028c7751b0db928a6a3bc351560b8b618"),
        ("https://downloads.sourceforge.net/project/arma/armadillo-14.2.3.tar.xz", "armadillo-14.2.3.tar.xz", "fc70c3089a8d2bb7f2510588597d4b35b4323f6d4be5db5c17c6dba20ab4a9cc"),
    ]
    archives.extend([
        ("https://codeload.github.com/google/XNNPACK/zip/e8f74a9763aa36559980a0c2f37f587794995622", "e8f74a9763aa36559980a0c2f37f587794995622.zip", "7a16ab0d767d9f8819973dbea1dc45e4e08236f89ab702d96f389fdc78c5855c"),
        ("https://codeload.github.com/Maratyszcza/FXdiv/zip/63058eff77e11aa15bf531df5dd34395ec3017c8", "63058eff77e11aa15bf531df5dd34395ec3017c8.zip", "3d7b0e9c4c658a84376a1086126be02f9b7f753caa95e009d9ac38d11da444db"),
        ("https://codeload.github.com/Maratyszcza/pthreadpool/zip/b8374f80e42010941bda6c85b0e3f1a1bd77a1e0", "b8374f80e42010941bda6c85b0e3f1a1bd77a1e0.zip", "b96413b10dd8edaa4f6c0a60c6cf5ef55eebeef78164d5d69294c8173457f0ec"),
        ("https://codeload.github.com/pytorch/cpuinfo/tar.gz/5e63739504f0f8e18e941bd63b2d6d42536c7d90", "5e63739504f0f8e18e941bd63b2d6d42536c7d90.tar.gz", "18eca9bc8d9c4ce5496d0d2be9f456d55cbbb5f0639a551ce9c8bac2e84d85fe"),
        ("https://codeload.github.com/google/ruy/zip/841ea4172ba904fe3536789497f9565f2ef64129", "841ea4172ba904fe3536789497f9565f2ef64129.zip", "dd6bf40322303cf8982f340e4139397c8fa350ff691d5254599cb21e0138fc65"),
    ])
    for url, filename, checksum in archives:
        download(url, cache / filename, checksum)
    bazel, version, qemu_compatibility = check_bazel(bazel, prefix, args.qemu_proc_self_workaround)
    argv = [str(bazel), f"--output_user_root={prefix / 'bazel-cache'}", "--batch", "build", ":visqol", "-c", "opt",
            f"--jobs={args.jobs}", f"--local_ram_resources={args.memory_mb}", "--nokeep_going", f"--distdir={cache}"]
    if system == "Darwin":
        # 旧 zlib 将现代 macOS 误判为无 fdopen 的 Classic Mac OS。
        argv.extend(["--per_file_copt=external/zlib/.*@-Dfdopen=fdopen", "--host_copt=-Dfdopen=fdopen"])
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    logs = prefix / "logs"; logs.mkdir(exist_ok=True)
    log = logs / ("build-" + stamp + ".log")
    env = dict(os.environ, PYTHON_BIN_PATH=sys.executable)
    if system == "Darwin":
        # Bazel 5 的 Xcode 包装器缺少 LC_UUID，macOS 26 dyld 拒绝执行。
        # 使用该版本自带的 C++ 工具链配置入口，直接调用本机编译器。
        env["BAZEL_USE_CPP_ONLY_TOOLCHAIN"] = "1"
    record = {"schema_version": "1.0", "status": "building", "source_commit": COMMIT,
              "source_archive_sha256": SOURCE_SHA256, "bazel_version": version, "bazel_sha256": digest(bazel),
              "platform": {"system": system, "machine": arch, "python": platform.python_version()},
              "command": argv, "log": str(log), "started_utc": stamp,
              "adjustments": ["用 curl 预取固定旧依赖的官方归档并校验，供 Bazel distdir 使用；未修改算法与模型"]}
    if system == "Darwin":
        record["adjustments"].extend(["macOS 使用 BAZEL_USE_CPP_ONLY_TOOLCHAIN=1，避免旧 Xcode 包装器缺少 LC_UUID",
                                      "zlib 目标编译及宿主工具链定义 fdopen=fdopen，保留系统 fdopen，避免旧 Classic Mac 分支与现代 SDK 冲突"])
    if qemu_compatibility:
        record["qemu_compatibility"] = qemu_compatibility
    manifest = prefix / "installation.json"
    if manifest.is_symlink():raise ValueError("安装记录目标不可为符号链接")
    manifest.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
    print(f"开始编译（首次需要联网），日志：{log}", flush=True)
    start = time.perf_counter()
    try:
        patch_info = patch_tflite_for_mac(prefix) if system == "Darwin" else None
        if patch_info:record["dependency_patch"] = patch_info
        record["build_logs"] = []
        for attempt in range(2):
            attempt_log = log if attempt == 0 else logs / ("build-" + stamp + "-retry.log")
            record["build_logs"].append(str(attempt_log))
            remaining = args.build_timeout - (time.perf_counter() - start)
            if remaining <= 0:raise subprocess.TimeoutExpired(argv, args.build_timeout)
            try:
                with attempt_log.open("wb") as stream:
                    run_build(argv, source, env, stream, remaining)
                break
            except subprocess.CalledProcessError:
                content = attempt_log.read_text(errors="replace")
                if system != "Darwin" or attempt or patch_info or "std::abs<float>" not in content:
                    raise
                patch_info = patch_tflite_for_mac(prefix)
                if not patch_info:raise
                record["dependency_patch"] = patch_info
                print(f"应用已校验的 TFLite libc++ 兼容补丁后重试，日志：{attempt_log}", flush=True)
        binary = source / "bazel-bin/visqol"
        if not binary.is_file() or not os.access(binary, os.X_OK):
            raise ValueError("构建返回成功但未生成可执行文件")
        record.update(status="built", binary_sha256=digest(binary), binary_bytes=binary.stat().st_size)
    except (subprocess.SubprocessError, OSError, ValueError, KeyboardInterrupt):
        record["status"] = "failed"
        print(f"构建未完成，查看 {log}；解决原因后可运行同一命令重试，保留缓存。", file=sys.stderr)
        raise
    finally:
        record["elapsed_s"] = time.perf_counter() - start
        manifest.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
    print(f"编译完成。下一步：voice-tools visqol doctor --visqol-dir '{source}'")
    print("还需运行 scripts/visqol_demo.py 验证实际评分；构建成功不代表你的音频已经通过评测。")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print(f"安装失败：{error}", file=sys.stderr)
        raise SystemExit(2)
