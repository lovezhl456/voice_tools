# ViSQOL：在 Docker 中运行 Linux CLI

[安装总说明](visqol.md) · [Dockerfile](../docker/visqol/Dockerfile) · [Linux 实测记录](visqol-linux-validation.md)

这条路线使用 Ubuntu 22.04 / **linux/amd64**。镜像包含 Python、voice_tools、编译工具和 FFmpeg；ViSQOL 官方源码、模型、可执行文件与构建缓存存入独立 Docker volume。安装完成后，评分容器可以禁用网络运行。

Mac Apple Silicon 上通过仿真运行 amd64；Linux x86_64 主机上运行同一镜像不需要跨架构仿真。本次本地 Docker 的结果和未验证范围见实测记录。

已实测：编译容器 4 CPU / 6 GiB，评分容器 2 CPU / 2 GiB；独立运行镜像约 193 MiB，无需 GPU。本次配置不是官方最低要求，M1/QEMU 的评分耗时也不是原生 Linux 服务器性能。

## 1. 构建工具镜像

准备已运行的 Docker，并下载本项目。在新的目录执行；功能尚未合并时使用当前功能分支：

```bash
git clone --branch feat/v0.11.1-visqol https://github.com/lovezhl456/voice_tools.git
cd voice_tools
```

已有仓库则使用对应工作分支，保留自己的未提交内容。以下命令均在仓库根目录执行：

```bash
docker build --platform linux/amd64 \
  -f docker/visqol/Dockerfile \
  -t voice-tools-visqol:0.11.1-amd64 .
```

Dockerfile 固定 Ubuntu 基础镜像摘要；APT 和 Python 依赖会在构建时下载。该镜像不是已经包含 ViSQOL 模型的即用镜像，继续下一步安装。

如网络必须经过已有代理，可以给 `docker build` 增加 `--build-arg HTTP_PROXY=... --build-arg HTTPS_PROXY=...`。Docker Desktop 访问宿主机时通常使用 `host.docker.internal`，具体端口取自己的配置；容器中的 `127.0.0.1` 指向容器自身。不要把代理密码写入 Dockerfile。

## 2. 安装 Linux 原生后端

```bash
docker volume create voice-tools-visqol-amd64

docker run --rm --platform linux/amd64 --cpus 4 --memory 6g --memory-swap 6g \
  --mount type=volume,src=voice-tools-visqol-amd64,dst=/opt/visqol \
  --entrypoint python voice-tools-visqol:0.11.1-amd64 \
  /opt/voice-tools/scripts/install_visqol.py \
  --prefix /opt/visqol --jobs 4 --memory-mb 4096 --build-timeout 14400 \
  --qemu-proc-self-workaround
```

第一次安装会下载和编译固定 ViSQOL 版本。4 小时参数是本命令的超时预算，不是安装耗时预测。失败后可使用相同 volume 和命令重试；不要为了重试删除缓存。构建日志在 volume 内的 `/opt/visqol/logs`，安装记录为 `/opt/visqol/installation.json`。

`--qemu-proc-self-workaround` 处理本次旧 Docker Desktop / QEMU 出现的 `Failed to open '/proc/self/exe' as a zip file`。只有匹配这一启动错误、且官方 Linux Bazel 二进制 SHA256 正确时才启用：编译一个小型文件打开兼容层，并创建 Bazel 启动脚本；官方二进制不变。兼容层加载后清除注入环境，不传给 Java 或编译器子进程。记录包含源码、库、启动脚本及原二进制校验值；正常启动的 Linux 主机不会启用这层处理。

如安装阶段需要代理，给 `docker run` 显式增加 `-e HTTPS_PROXY=... -e HTTP_PROXY=...`；Git 等工具需要时同时设置小写变量。构建镜像时的代理参数不会自动应用到后续容器。

## 3. 检查与真实演示

检查只读后端文件：

```bash
docker run --rm --platform linux/amd64 --network none \
  --mount type=volume,src=voice-tools-visqol-amd64,dst=/opt/visqol,readonly \
  voice-tools-visqol:0.11.1-amd64 visqol doctor
```

运行官方样本演示，将结果保存在宿主机：

```bash
mkdir -p outputs/visqol-linux
docker run --rm --platform linux/amd64 --network none --cpus 2 --memory 2g \
  --mount type=volume,src=voice-tools-visqol-amd64,dst=/opt/visqol,readonly \
  --mount type=bind,src="$PWD/outputs/visqol-linux",dst=/results \
  --entrypoint python voice-tools-visqol:0.11.1-amd64 \
  /opt/voice-tools/scripts/visqol_demo.py --out /results/demo-001
```

每次使用新的输出子目录。演示包括同文件对比、官方转码、人工加噪和一般音频模式基准；详细分数和命令保存在 `demo.json` 及各评分目录。

需要复验本轮的基准、失败处理与资源测量时，在上面的演示命令中，将脚本改为 `/opt/voice-tools/scripts/visqol_linux_smoke.py`，并使用新的 `--out /results/validation-001`。该脚本执行三个上游短样本基准、真实 CLI 评分、失败项在前的混合批次、六次 GNU time 测量，并生成 `validation.json`。三个基准沿用上游原始 48 kHz 样本口径，不与 CLI 重采样后的 16 kHz 分数硬比；它不替代上游完整基准测试。

## 4. 对自己的音频评分

将已经对应、切片并准备好的单声道 PCM16 WAV 放在 `data/visqol`。默认语音模式要求 16 kHz；不能用主叫和被叫的不同语句互作参考。

```bash
mkdir -p outputs/visqol-linux
docker run --rm --platform linux/amd64 --network none --cpus 2 --memory 2g \
  --mount type=volume,src=voice-tools-visqol-amd64,dst=/opt/visqol,readonly \
  --mount type=bind,src="$PWD/data/visqol",dst=/data,readonly \
  --mount type=bind,src="$PWD/outputs/visqol-linux",dst=/results \
  voice-tools-visqol:0.11.1-amd64 --json visqol score \
  --reference /data/reference16.wav --degraded /data/received16.wav \
  --out /results/score-001
```

批量时，将最后三行命令改为 `voice-tools-visqol:0.11.1-amd64 --json visqol batch --pairs /data/pairs.csv --out /results/batch-001`。CSV 中的相对音频路径以容器内 CSV 所在目录为准，不要填只有宿主机才存在的绝对路径。

结果文件会回到宿主机绑定目录；JSON 记录的是容器内路径。必要时在 Linux 宿主机的评分命令中加 `--user "$(id -u):$(id -g)"`，让输出归当前用户所有；安装阶段仍用镜像默认用户。

volume 同时保存 `bazel-bin` 链接所指向的构建缓存，需完整挂载。`--rm` 只删除该次容器，不会删除命名 volume 或宿主机结果。镜像默认入口就是 `voice-tools`，其 `--json`、单对/批量退出码及结果格式与本机 CLI 相同。

## 5. 生成不依赖构建缓存的运行镜像

后端安装成功后，可以只导出程序和两个模型，再构建日常评分用的镜像。导出脚本把链接目标复制成普通文件，并检查复制前后 SHA256；原构建 volume 保留。

```bash
mkdir -p outputs/visqol-runtime-context
docker run --rm --platform linux/amd64 --network none \
  --mount type=volume,src=voice-tools-visqol-amd64,dst=/opt/visqol,readonly \
  --mount type=bind,src="$PWD/outputs/visqol-runtime-context",dst=/export \
  --entrypoint python voice-tools-visqol:0.11.1-amd64 \
  /opt/voice-tools/scripts/visqol_export_runtime.py --out /export/backend

docker build --platform linux/amd64 \
  -f docker/visqol/Dockerfile.runtime \
  -t voice-tools-visqol-runtime:0.11.1-amd64 \
  outputs/visqol-runtime-context

docker run --rm --platform linux/amd64 --network none \
  voice-tools-visqol-runtime:0.11.1-amd64 visqol doctor
```

使用运行镜像评分时，沿用第 4 节命令，将镜像名改为 `voice-tools-visqol-runtime:0.11.1-amd64`，并去掉 `/opt/visqol` 的 volume 挂载；只挂载输入和结果目录。

运行镜像不含编译器、Bazel、源码缓存、FFmpeg 或演示音频；输入须已准备为正确 WAV。格式转换与官方样本演示使用前面的工具镜像。导出不是跨系统或跨架构转换；本指南只针对同一 Ubuntu / amd64 运行环境。
