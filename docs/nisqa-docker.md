# NISQA：Linux CPU Docker 部署

使用仓库提供的 `docker/nisqa.Dockerfile` 构建镜像，CLI 与 [NISQA 手册](nisqa.md) 相同。模型单独下载到宿主机目录；镜像及 Git 中均不放权重或录音。支持的架构和本轮实测范围见 [Linux 验收记录](nisqa-linux-validation.md)。

## 1. 构建镜像

在仓库根目录、Docker 已运行的机器上执行：

```bash
docker build -f docker/nisqa.Dockerfile -t voice-tools-nisqa:0.10.2 .
```

默认使用官方 `python:3.12-slim-bookworm` 和本机架构；先从 PyTorch CPU 索引安装 Torch，再安装 NISQA 可选依赖，避免 x86 Linux 拉入 CUDA 包。基础镜像、Python 依赖下载均需要网络。`.dockerignore` 只允许打包所需源码进入构建上下文，排除权重、录音、虚拟环境、输出和 Git 历史。

跨架构时显式添加 `--platform linux/arm64` 或 `--platform linux/amd64`，并使用不同镜像标签。Mac 上模拟 amd64 只适合验证兼容性，其速度不能作为 x86 服务器的容量依据。构建后可保存镜像 ID、基础镜像 digest 和 `python -m pip freeze` 输出作为本地部署清单；后续使用同一镜像 ID 可避免标签更新。

## 2. 显式下载权重

先创建本地模型、输入和输出目录，再下载：

```bash
mkdir -p .local/nisqa-model data/calls outputs

docker run --rm --user "$(id -u):$(id -g)" \
  --mount "type=bind,src=$PWD/.local/nisqa-model,dst=/models" \
  voice-tools-nisqa:0.10.2 nisqa download --model-dir /models
```

只有这一步访问官方固定版本权重 URL。已存在且哈希正确的文件会直接复用。也可以从已验证的 Mac 环境复制 `nisqa.tar` 到该目录；这不等于验证了容器的下载网络。权重采用 CC BY-NC-SA 4.0，商业使用仍须解决授权。

容器默认使用普通用户 `10001:10001`；示例用宿主 UID/GID 运行，便于 Linux 上向挂载目录写入结果。不要靠 `chmod 777` 或提升整个容器权限解决目录归属。

## 3. 断网检查与批量评分

```bash
docker run --rm --network none --read-only \
  --user "$(id -u):$(id -g)" \
  --mount "type=bind,src=$PWD/.local/nisqa-model,dst=/models,readonly" \
  voice-tools-nisqa:0.10.2 --json nisqa doctor --model-dir /models

# 把双声道 WAV/FLAC 录音放进 data/calls；输出子目录每次使用新名字。
docker run --rm --network none --read-only \
  --cpus 2 --memory 2g --memory-swap 2g \
  --user "$(id -u):$(id -g)" \
  --tmpfs /tmp:rw,nosuid,size=512m \
  --mount "type=bind,src=$PWD/.local/nisqa-model,dst=/models,readonly" \
  --mount "type=bind,src=$PWD/data/calls,dst=/input,readonly" \
  --mount "type=bind,src=$PWD/outputs,dst=/output" \
  voice-tools-nisqa:0.10.2 --json nisqa analyze /input \
  --model-dir /models --channel both --threads 2 \
  --out /output/nisqa-batch-001
```

单声道省略 `--channel both`；混合声道目录应按录制配置分批处理。2 核 / 2 GiB 是此示例的容器资源限制，不是官方最低配置或所有批量任务的容量保证。`/tmp` 用于 Numba 等临时缓存，计入容器内存；每次新容器的这份缓存均从空开始。

输入与模型只读挂载，结果保存在宿主 `outputs/nisqa-batch-001`，退出后仍可查看。`--network none` 断开容器的外部网络，容器内回环通信仍可使用。退出码和静音/短段/部分失败的含义与本地 CLI 一致；`doctor` 仅检查依赖元数据和权重，真正执行验证要运行 `analyze` 或下面的脚本。

## 4. 记录执行耗时和内存

使用经过授权的单声道 `data/calls/speech.wav`：

```bash
docker run --rm --network none --read-only \
  --cpus 2 --memory 2g --memory-swap 2g \
  --user "$(id -u):$(id -g)" \
  --tmpfs /tmp:rw,nosuid,size=512m \
  --mount "type=bind,src=$PWD/.local/nisqa-model,dst=/models,readonly" \
  --mount "type=bind,src=$PWD/data/calls,dst=/input,readonly" \
  --mount "type=bind,src=$PWD/outputs,dst=/output" \
  --mount "type=bind,src=$PWD/scripts,dst=/scripts,readonly" \
  --entrypoint python voice-tools-nisqa:0.10.2 \
  /scripts/nisqa_smoke.py /input/speech.wav \
  --model-dir /models --out /output/nisqa-smoke-001
```

脚本输出 `smoke.json`，记录真实推理是否成功、CLI 子进程墙钟耗时和峰值 RSS。时间不含 Docker 拉取/构建/启动，RSS 不等于整个 Linux 虚拟机或容器的内存。评估生产容量时使用真实录音、明确首次缓存状态，并测量整批完成时间及失败率。

## 5. 离线专项回归

模型和 `data/calls/speech.wav` 已准备好时，可在运行镜像内验证 22 项 NISQA 测试，包括真实模型对齐：

```bash
docker run --rm --network none --read-only \
  --cpus 2 --memory 2g --memory-swap 2g \
  --user "$(id -u):$(id -g)" \
  --tmpfs /tmp:rw,nosuid,size=512m \
  --mount "type=bind,src=$PWD,dst=/repo,readonly" \
  --mount "type=bind,src=$PWD/.local/nisqa-model,dst=/models,readonly" \
  --mount "type=bind,src=$PWD/data/calls/speech.wav,dst=/input/speech.wav,readonly" \
  --workdir /repo \
  --env VOICE_TOOLS_NISQA_MODEL_DIR=/models \
  --env VOICE_TOOLS_NISQA_AUDIO=/input/speech.wav \
  --entrypoint python voice-tools-nisqa:0.10.2 \
  -m unittest discover -s tests/nisqa -v
```

整个仓库的测试另外需要 Node.js、tshark 等既有业务工具；NISQA 运行镜像不包含这些可选组件。本轮全仓回归的额外测试环境和跳过项见验收记录。

## 网络与复现

Docker 拉基础镜像、构建时 pip 下载、运行时权重下载是三段不同的网络路径；其中一段成功不证明另外两段可用。若已有代理，可按自己的网络要求给本次构建传 `HTTP_PROXY/HTTPS_PROXY` build args，或只给下载容器传相应环境变量；不要写入 Dockerfile、镜像或仓库。Mac 容器访问宿主服务通常使用 `host.docker.internal`，不能直接把宿主的 `127.0.0.1` 当成容器外的代理。

完全离线的 Linux 机器可导入已构建的目标架构镜像和已校验权重，再执行断网命令。部署镜像后无需在 Linux 宿主安装 Python/Torch，也不依赖 Mac 上的虚拟环境。
