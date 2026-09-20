# latency 外部依赖安装说明

本说明适用于 voice_tools 0.15.1 的受控适配版。先了解 [输入与结果](latency.md)，再按平台选择环境；[实际验证记录](latency-validation.md) 区分已执行与未验证范围。

## 原生 macOS / Linux

主包仍支持 Python 3.9+，引擎单独使用 Python 3.11 或 3.12。已有 Python 3.11/3.12 和可用的依赖来源是首次安装前提。安装器随 wheel/sdist 分发，仓库 `scripts/install_latency.py` 只是入口。

```sh
python -m pip install ./dist/voice_tools-0.15.1-py3-none-any.whl
python -m voice_tools.tools.latency.install --python /absolute/path/to/python3.12
voice-tools latency doctor
```

默认前缀 `~/.local/share/voice-tools/latency`；参数应使用最终规范路径，不能是符号链接。源码固定于 `0d71ca1f42a59744258420e4074ae0378f86c857`。安装器校验源码归档、检测器原文、补丁和许可，创建最终位置的 venv，安装固定 NumPy 2.2.6，检查可导入性后原子写入 `ready.json`。不移动已经创建的 venv。

本适配器直接加载固定源码中的 `FinalDetector`，PCM 由标准库读取，检测运算仅用 NumPy。上游完整 CLI、librosa 加载器、Web 服务和单声道说话人分类未启用，因此不安装其 librosa/scipy/click/pydub/resemblyzer 依赖，也不提供上游 `audio-analyze` 命令。固定依赖清单在包内 `latency/resources/requirements.txt`。

```sh
python -m voice_tools.tools.latency.install --python /absolute/path/to/python3.11 --prefix /absolute/path/to/latency-v1
voice-tools --json latency doctor --latency-dir /absolute/path/to/latency-v1
```

路径优先级：命令 `--latency-dir` → `VOICE_TOOLS_LATENCY_DIR` → 默认前缀。doctor 检查版本、摘要、路径、Python/NumPy 和当前架构，但不做录音测量。只有显式安装联网；doctor/analyze/batch 不下载、不修复。引擎使用 `-I`、独立进程组和进程内线程/缓存配置，不修改主进程的 Python 搜索路径。临时文件位于系统临时目录，结果目录不收集缓存或虚拟环境。

## 升级、回退、卸载

一个前缀对应一个固定版本。同版本重复安装且 doctor 通过时为幂等检查；未知、未完成、校验损坏的目录一律拒绝覆盖。同前缀用独占 `.install.lock` 防止并发。失败保留 `installing.json` 供排错，不写成功标记。

升级安装到新前缀，先 doctor，再实际分析固定样本，然后显式切换：

```sh
python -m voice_tools.tools.latency.install --python /absolute/path/to/python3.12 --prefix /absolute/path/to/latency-next
voice-tools latency doctor --latency-dir /absolute/path/to/latency-next
voice-tools latency analyze demo/audio/normal.wav --system-channel right --latency-dir /absolute/path/to/latency-next --out results/upgrade-check
export VOICE_TOOLS_LATENCY_DIR=/absolute/path/to/latency-next
# 回退：重新指定保留的旧前缀，再检查和分析；不移动环境。
export VOICE_TOOLS_LATENCY_DIR=/absolute/path/to/latency-v1
```

卸载时先停止使用该前缀的任务，只删除明确属于该安装的前缀目录并取消对应环境变量/执行机配置；不要删除仍在用的旧前缀。残留安装锁须先确认没有安装进程，再人工移除；安装器不会擅自判断和删锁。

## Docker 与跨机离线运行

专用 Dockerfile 以已有任务执行镜像为基础，安装当前 voice_tools 与独立引擎；原 Dockerfile 与默认镜像不新增 latency 依赖。需要先按 [任务手册](task-delivery.md) 准备对应架构的基础镜像。

```sh
docker build -f docker/latency/Dockerfile --build-arg EXECUTOR_IMAGE=voice-tools-executor:0.13.1 -t voice-tools-latency:0.15.1 .
docker run --rm --network none --read-only --tmpfs /tmp:rw,exec,size=512m --user 10001:10001 voice-tools-latency:0.15.1 --json latency doctor
VT_IMAGE=voice-tools-latency:0.15.1 ./vt --help
```

`VT_IMAGE` 显式选择镜像，不改变 `vt prepare` 的原有行为。镜像内固定前缀 `/opt/latency`，可在执行机配置填 `latency_dir`，或使用镜像设置的环境变量。挂载的结果目录应允许执行用户写入；只读根文件系统时必须给 `/tmp` 可写空间。普通录音分析不需要业务网络。

离线跨机首版采用**相同架构的已构建镜像**：

```sh
docker image inspect voice-tools-latency:0.15.1 --format '{{.Os}}/{{.Architecture}}'
docker save -o voice-tools-latency-0.15.1.tar voice-tools-latency:0.15.1
# 在同架构目标机：
docker load -i voice-tools-latency-0.15.1.tar
docker run --rm --network none voice-tools-latency:0.15.1 --json latency doctor
```

原生 venv 不作为跨机或跨架构部署包。Docker 禁网运行、镜像导入后的首次运行，与“在离线机器上首次安装原生依赖”不同。安装器 `--archive` 仅接受已下载且摘要匹配的源码；依赖仍需完整可用来源。pip 的普通缓存不保证完整离线 wheelhouse，首版不承诺原生离线首次安装。

## 排错

| 表现 | 处理 |
| --- | --- |
| 依赖缺失/doctor 失败 | 核对选中的前缀、Python 版本、NumPy、ready.json 及错误明细；安装到新前缀 |
| 前缀已存在、缺少 ready.json | 检查 installing.json/安装日志；保留证据并改用新前缀 |
| 归档或补丁摘要错误 | 停止使用该下载，核对固定提交与包内 manifest；不要忽略校验 |
| 命令行安装成功但任务失败 | 检查执行端路径和架构；本机路径不随任务包搬运 |
| 原生下载失败 | 检查依赖来源可达性；已建 venv 不会被移动或静默复用 |
| 超时、取消、崩溃 | 看 files.jsonl 的错误、中断与已完成记录；需要时调文件/步骤超时 |
| 页面可见但不能跳转 | 检查是否包含音频，服务器是否支持 Range/206；使用仓库服务器 |
