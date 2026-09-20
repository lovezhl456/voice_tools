# ViSQOL 全参考音质评估

[返回工具导航](../README.md) · [本机实战记录](visqol-local-validation.md) · [安装脚本](../scripts/install_visqol.py) · [可运行演示](../scripts/visqol_demo.py)

ViSQOL 把**干净原声**和**同一句话的待测版本**成对比较，输出 MOS-LQO 听感估计。它适合固定语句线路测试、编解码对比，以及保留 TTS 原始输出后的传输损失评估。它不是 ASR、音频修复器或单端质量模型，不能拿主叫和被叫互作参考，不能用低分直接判断故障原因。

本工具将 Google 官方 ViSQOL CPU 程序作为可选后端，通过独立子进程调用。常规 `pip install voice-tools` 不会下载 ViSQOL 或 TensorFlow 构建依赖；其余工具不受影响。**只有安装阶段需要联网，评分在本机 CPU 上离线进行，不需要 GPU 或 API Key。**

## 1. 下载与安装

### 1.1 准备 voice_tools

在本仓库根目录执行。使用 Python 3.9–3.11 的独立虚拟环境，兼容这条固定的旧原生构建链。普通 voice_tools 的其他功能仍按项目本身的 Python 支持范围运行。

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install -e .
voice-tools --version
```

若下载本项目，先使用 `git clone https://github.com/lovezhl456/voice_tools.git`，进入仓库；尚未合并的功能需要切换到 `feat/v0.11.1-visqol`。不要为安装本功能覆盖自己已有的工作树或未提交修改。

### 1.2 系统前置工具

macOS：安装 Apple Xcode Command Line Tools（`xcode-select --install`），准备 Git、curl 和 Python。演示脚本需要 FFmpeg；已使用 Homebrew 的机器可按需 `brew install ffmpeg`，已有的不用重复安装。

Ubuntu 22.04 x86_64 是可选部署路线，使用 Python 3.10；本次实际验收平台以[实战记录](visqol-local-validation.md)为准。常见前置包：

```bash
sudo apt-get update
sudo apt-get install -y build-essential git curl unzip zip python3-dev python3-venv python3-pip ffmpeg
```

安装脚本提供 macOS arm64/x86_64 与 Linux x86_64 路线，不能把未实际验收的平台说成已通过。其他架构与 Windows 请按上游构建说明处理；Windows 官方属于实验支持。

### 1.3 一条脚本安装后端

仍在仓库根目录、虚拟环境已激活时执行：

```bash
python scripts/install_visqol.py --prefix "$PWD/.local/visqol" --jobs 2
export VOICE_TOOLS_VISQOL_DIR="$PWD/.local/visqol/source"
voice-tools visqol doctor
```

脚本固定：

- ViSQOL 提交 `38d0b0163e441047d4429bf07ad09e5b9031d02c`，校验源码归档 SHA256。
- Bazel `5.1.0`，下载对应系统的官方可执行文件并验证校验值；可用 `--bazel /path/to/bazel` 指定已有的同版本程序。
- 默认两路编译、4096 MB 的 Bazel **调度预算**；这不是系统层面的硬内存限制。`--jobs`、`--memory-mb` 可调。
- `PREFIX/source` 保存官方源码与模型，`PREFIX/bazel-cache` 保存构建缓存，`PREFIX/logs` 保存每次完整构建日志，`PREFIX/installation.json` 保存版本、命令、状态与校验值。
- 所有依赖与产物留在所选前缀，不创建守护服务，不修改系统代理、系统 Python 或 shell 配置。

`--prefix` 省略时使用 `~/.local/share/voice-tools/visqol`。CLI 的 `--visqol-dir` 或 `VOICE_TOOLS_VISQOL_DIR` 指向其中的 **source 目录**，不是前缀目录。显式参数优先于环境变量；都未设置时使用默认安装位置。

`doctor` 仅检查二进制、模型文件和执行权限，并展示安装记录；它不代表实际评分已经成功。继续下一节跑演示。

### 1.4 下载、编译失败怎么办

先查看脚本打印的构建日志。不要删除整套缓存来试运气；修复原因后可以使用同一命令重试。已有文件的校验不符、源码有本地修改或目录存在未知内容时，脚本会拒绝覆盖。

上游固定版本的部分旧下载入口已经失效，安装脚本用系统 curl 预取 TFRT、Protobuf、构建规则、LibSVM、Armadillo 与相关 TFLite CPU 依赖 的同一官方归档，再通过 Bazel 的 distdir 缓存使用；GitHub 归档从其官方 codeload 服务获取。依赖校验仍由上游固定 SHA256 检查，不能关闭校验或换成来源不明的二进制。macOS 使用 Bazel 的 `BAZEL_USE_CPP_ONLY_TOOLCHAIN=1` 入口绕过缺少 LC_UUID 的旧 Xcode 包装器，并为旧 zlib 的目标/宿主编译设置 `fdopen=fdopen` 兼容定义。另外，脚本只对固定文件 SHA256 的 TensorFlow Lite 2.11 `elementwise.cc` 应用一处等价 `std::abs(float)` 重载补丁，解决现代 libc++ 不接受 `std::abs<float>` 的编译错误；其他内容不匹配时拒绝修改。安装记录包含补丁前后校验值。ViSQOL 自身评分代码和模型未改，依赖兼容补丁仍须通过实战基准验证。具体兼容处理见实战记录。

若当前网络必须经过已有代理，可只给本次命令设置 `HTTPS_PROXY` / `HTTP_PROXY`（使用你已配置的真实地址）。这不是必须设置，也不要复制别人的本机端口作为自己的代理。认证信息不要写入仓库或命令日志。

`--download-cache /path/to/cache` 可复用已经校验的源码和依赖归档。构建默认限时 3600 秒，可用 `--build-timeout` 调整。首次构建会下载大量依赖；“CPU 运行”并不代表首次编译只需几 MB。

## 2. 最快验证：运行官方样本演示

```bash
python scripts/visqol_demo.py \
  --visqol-dir "$VOICE_TOOLS_VISQOL_DIR" \
  --out outputs/visqol-demo-001
```

演示从你下载的官方源码取 `testdata/clean_speech` 样本，使用 FFmpeg 显式转换到 16 kHz，然后实际通过 **voice_tools CLI** 执行：

1. 同一文件对比，验证基准流程；不强行要求默认 lattice 模型结果精确等于 5。
2. 原声与官方转码版本、原声与固定种子人工加噪版本的两对批量评分。
3. 原始 48 kHz 文件的一般音频模式评分，并与固定上游 `kCA01_01AsAudio` 基准 `1.7658378752958486` 比较，容差 `0.0001`。

`demo.json` 记录每条命令、输入校验值、转换方式及输出摘要；每次使用新目录。官方样本约 2.74 秒，只用于验证接入；人工加噪是对照，不能冒充真实通话故障或中文业务验收。

## 3. 评分前准备文件

本 CLI 的明确输入边界：

- 两份都是**单声道 PCM16 WAV**。语音模式 `speech` 要求 16 kHz，一般音频模式 `audio` 要求 48 kHz。
- 每份 1–60 秒、文件不超过 16 MiB；建议约 3–10 秒对应有声片段。这是适配层的资源与输入约束，不是声称上游只能处理这些时长。
- 完全静音、文件截断、错误采样率、双声道、非法 WAV 会失败，不输出分数。
- 低能量、触顶样本、时长超出建议范围或两份时长差超过 0.5 秒会留下提示。这些提示不是自动确认的故障。
- 调用者负责确认“同一句、同一对应片段”。代码不能从格式检查证明语义配对。

如果已经是正确单声道，只做必要的显式格式转换：

```bash
mkdir -p data/visqol
ffmpeg -i original.wav -ar 16000 -ac 1 -c:a pcm_s16le data/visqol/reference16.wav
ffmpeg -i received.wav -ar 16000 -ac 1 -c:a pcm_s16le data/visqol/received16.wav
```

如果双声道分别存放双方，不要直接 `-ac 1` 混音。先确认角色所在的声道，再分别提取，例如左声道：

```bash
ffmpeg -i call-stereo.wav -af 'pan=mono|c0=c0' -ar 16000 -c:a pcm_s16le data/visqol/left16.wav
```

右声道使用 `pan=mono|c0=c1`。左声道不一定是主叫，应以采集定义为准。8 kHz 电话音频升采样到 16 kHz 不会补回高频；保留原始带宽信息和预处理口径，不能据此硬套宽带质量阈值。

## 4. 单对 CLI

```bash
voice-tools visqol score \
  --reference data/visqol/reference16.wav \
  --degraded data/visqol/received16.wav \
  --mode speech \
  --out outputs/visqol-score-001
```

也可不设置环境变量，每次加 `--visqol-dir /absolute/path/to/source`。`speech` 是此适配层的默认模式；上游裸 CLI 默认 audio，两者不要混淆。

一般音频准备为 48 kHz 后使用 `--mode audio`。不同模式或不同模型的数字不应直接混排。CLI 不提供未经验证的通用“低于 X 就报警”阈值。

## 5. 批量 CLI

[CSV 示例](../examples/visqol/pairs.example.csv)格式如下，路径相对于 **CSV 文件所在目录**，与运行命令的位置无关：

```csv
reference,degraded
./reference16.wav,./received16.wav
./reference16_b.wav,./received16_b.wav
```

```bash
voice-tools --json visqol batch \
  --pairs data/visqol/pairs.csv \
  --mode speech --timeout 120 \
  --out outputs/visqol-batch-001
```

按 CSV 顺序、单进程逐对运行；每对默认超时 120 秒，可设 0–3600 秒之间的正数。单对失败会继续后续音频，失败行的 `moslqo` 为 `null`，不会伪造成零分。单批最多 10000 对，CSV 最大 5 MiB。

文件名有逗号时按标准 CSV 引号规则写入。适配层读取官方 JSON 的完整精度分数，并生成规范 CSV；上游 `upstream.csv` 的路径转义有限，仅保留为原始证据，不用它充当规范结果。

## 6. 产物与退出码

输出目录必须不存在或为空，避免把旧结果混入新结果。

| 文件 | 用途 |
|---|---|
| `run.json` | 批次摘要、工具/后端版本信息、二进制与模型校验值、局限 |
| `results.jsonl` | 每对一行，完成/失败、模式、原输入路径、分数、提示与输入元数据 |
| `results.csv` | 便于表格软件读取的标准 CSV；失败分数留空 |
| `pairs/00001/reference.wav` / `degraded.wav` | 本次实际输入快照，保证评分后可追溯 |
| `pairs/00001/invocation.json` | 参数数组、运行目录与超时值 |
| `pairs/00001/upstream.json` / `upstream.csv` | 原生后端的完整结果 |
| `pairs/00001/stdout.log` / `stderr.log` | 原生程序日志；不污染顶层 JSON stdout |
| `pairs/00001/result.json` | 单对状态与错误；输入检查失败时不会启动后端 |

快照包含音频本身，留存和共享时按你的录音数据管理规则处理。脚本不会将评分输入上传。

| 退出码 | 含义 |
|---|---|
| `0` | 本次全部音频对计算完成，或 doctor 文件检查通过；不等于音质达标 |
| `2` | 参数、批次清单、依赖或输出目录等整体前置条件不满足 |
| `3` | 至少一对输入或后端计算失败（包括全部失败）；检查保留产物 |

`--json` 放在 `visqol` 前。统一封套中的 `ok=false`、`exit_code=3` 表示未完整完成；均分只包含成功项，不能冒充整个批次的质量。需要大模型调用时，先运行 `voice-tools schema --tool visqol`，再通过 subprocess 参数数组调用，禁止拼接未经转义的 shell 字符串。

## 7. 资源与适用范围

运行只需 CPU。源码构建和最终评分是不同开销：首次需要工具链、较大的 TensorFlow/TFLite 等源码依赖与编译缓存，运行时使用小得多的本地程序和模型。建议先少量短片段、单进程测量，再决定并发。

实际本机版本、编译情况、单对耗时、程序/模型大小与峰值内存，见[本机实战记录](visqol-local-validation.md)。不要把这台机器的短样本结果推广为所有服务器或长通话的吞吐保证；冷启动、音频时长、模式和并发均影响开销。

推荐接入位置：固定测试语句/TTS 原始输出 → 接收端录音 → 对齐切片与显式格式准备 → ViSQOL → 异常样本试听。只有日常通话录音、没有参考原声时，需要选择单端估计或其他质检手段。延迟、回声、打断、业务话术等指标另行测量。

## 来源

- [Google ViSQOL 固定提交 README](https://github.com/google/visqol/blob/38d0b0163e441047d4429bf07ad09e5b9031d02c/README.md)
- [固定提交构建依赖](https://github.com/google/visqol/blob/38d0b0163e441047d4429bf07ad09e5b9031d02c/WORKSPACE)
- [Bazel 5.1.0 官方发行](https://github.com/bazelbuild/bazel/releases/tag/5.1.0)

ViSQOL 上游为 Apache-2.0；安装目录包含其 LICENSE。各构建依赖的许可证独立适用。仓库不打包原生依赖、模型、录音或本地构建缓存。
