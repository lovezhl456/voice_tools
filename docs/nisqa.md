# NISQA：下载、安装与本地录音听感评分

`voice-tools nisqa` 使用 NISQA v2.0 的通话五维权重，在本机 CPU 上输出整体质量、噪声、断续、音色、响度的预测分数。它是录音质检的补充，不自动修音，也不证明故障根因。这里只提供通话模型，不包含 TTS 自然度模型或训练功能。

代码、CLI、下载逻辑和使用文档进入 Git；**权重、音频、虚拟环境及运行产物不进入 Git**。无需云模型 API，只有用户显式执行 `download` 时访问官方权重地址。

## 权重许可

本项目自有代码的 Apache-2.0 授权不覆盖 NISQA 官方模型权重。权重不随本项目源码、安装包或 Docker 镜像分发，由用户显式下载。

原 NISQA 代码为 MIT，但官方 `nisqa.tar` 权重是 **CC BY-NC-SA 4.0，含非商业限制**。用于商业客服生产系统前须另行解决授权；换成 TorchMetrics 封装不会改变权重许可。参见[原项目 README](https://github.com/gabrielmittag/NISQA)和[固定版本权重许可](https://github.com/gabrielmittag/NISQA/blob/fe84f0f252abec382b24367d5b22498a7ce34dbb/weights/LICENSE_model_weights)。

## 安装

Docker 部署见 [Linux CPU 容器指南](nisqa-docker.md)，可将 Python 及依赖封装在镜像中，权重和录音单独挂载。

基础工具仍支持 Python 3.9+；**NISQA 可选依赖要求 Python 3.10+**，建议独立的 Python 3.11/3.12 环境。在仓库根目录执行。

Linux CPU（以下假设已安装 Python 3.11 和 venv 支持）：

```bash
python3.11 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install 'torch>=2.6,<3' --index-url https://download.pytorch.org/whl/cpu
python -m pip install -e '.[nisqa]'
python -m pip check
```

Linux 先安装 CPU wheel，避免默认安装带入不需要的 CUDA 依赖。不同 CPU 架构的 wheel 可用性依 PyTorch 官方分发为准。

Apple Silicon Mac（原生 arm64 Python，无需 CUDA）：

```bash
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[nisqa]'
python -m pip check
```

当前封装在 CPU 上执行，不会自动使用 MPS/CUDA。基础 `pip install -e .` 不安装 Torch 或其他 NISQA 依赖；普通 `--help`、`schema` 和 `nisqa doctor` 不导入推理运行库。

实现固定使用 `torchmetrics==1.9.0` 的模型与前处理函数，经过真实模型对齐测试后再升级该依赖。其余依赖版本可用 `python -m pip freeze` 保存到本地锁定清单。已测环境分别记录在 [Mac 验收](nisqa-validation.md)和 [Linux Docker 验收](nisqa-linux-validation.md)；容器及模拟架构结果不代表真实服务器容量。

## 显式下载与离线检查

```bash
voice-tools nisqa download
voice-tools --json nisqa doctor
voice-tools schema --tool nisqa
```

默认目录是 `$XDG_CACHE_HOME/voice-tools/nisqa`；未设置时为 `~/.cache/voice-tools/nisqa`。也可以选择一个 Git 忽略的本地目录：

```bash
voice-tools nisqa download --model-dir .local/nisqa-model
voice-tools nisqa doctor --model-dir .local/nisqa-model
```

下载固定到上游提交 `fe84f0f252abec382b24367d5b22498a7ce34dbb` 的 `weights/nisqa.tar`。文件为 1,051,663 字节；SHA-256 为：

```text
7ec4cf937514dd3f8860b21e66fabd8ca87a168572675ef8d979c4c4ad2e805c
```

- 下载到临时文件，限制大小并校验哈希，通过后才替换目标；失败不损坏已有权重。
- 已存在且校验通过时直接复用，不访问网络。损坏文件会报错，可用相同命令加 `--force` 重新下载。
- 离线服务器可从已校验的机器复制这一个 `nisqa.tar` 到指定目录，再运行 `doctor`。不要把权重加到 Git；`.gitignore` 已排除常见模型目录和权重后缀。
- `doctor` 检查依赖的安装元数据和权重 SHA-256，**不证明运行库已经成功加载或模型能推理**。实际运行检查使用后面的 smoke 脚本。
- `analyze` 在反序列化前验证本地字节，然后用 `weights_only=True` 加载。没有使用 TorchMetrics 的自动下载入口，缺少/损坏权重会直接失败。

## 单文件与批量 CLI

单声道录音：

```bash
voice-tools --json nisqa analyze data/mono.wav --out outputs/nisqa-001
```

双声道需要你确认录制配置，然后显式选择：

```bash
# 分别评价两个声道，不混音、不推断谁是用户/AI
voice-tools --json nisqa analyze data/call.wav --channel both --out outputs/nisqa-002

# 只评价右声道
voice-tools nisqa analyze data/call.wav --channel right --out outputs/nisqa-003

# 递归处理目录下的 WAV / FLAC；权重目录须与下载时一致
voice-tools --json nisqa analyze data/calls \
  --channel both --segment-seconds 10 --threads 2 \
  --model-dir .local/nisqa-model --out outputs/nisqa-batch-001
```

可以传多个文件或目录，重复路径会去重。支持 8～96 kHz 的单声道或双声道 WAV/FLAC。单声道可以省略 `--channel` 或使用 `left`；`right/both` 对单声道报错，避免伪造声道。混合目录中不符合声道选择的文件会明确失败，其他文件继续。

MP3/M4A/G.711 裸流等应先用 [audio prepare](manual.md#1-体检与格式准备) 明确格式、保留声道再转换，保留录制点和时间映射。不在 NISQA 中隐式归一化、重采样、降噪或合并声道。

### 分段与证据不足

- 默认每段 10 秒，允许 `--segment-seconds 1..20`；长通话逐块读取，不一次载入全部波形。窗口长度会影响评分，版本对比应保持相同分段策略。
- 默认不足 1 秒的尾段标为 `insufficient_evidence / too_short`，不打分；`--min-seconds` 可调，最低 0.5 秒。
- 空音频、纯静音或 RMS 低于 `--min-rms-dbfs`（默认 -60 dBFS）的片段不给分，分数字段为 null。
- **RMS 门槛不是 VAD**，噪声和音调可能通过。低音量真人语音也可能被拒绝，需按实际样本检查门槛；不能把“有分数”当作“检测到人声”。
- NaN/Infinity 音频或模型分数作为错误记录，不写入伪造分数。
- 不用固定“3 分合格”等阈值；中文业务准确度需要本地人工评分验证。

## 输出与退出码

每次使用新的或空的输出目录，拒绝覆盖已有内容。产生：

| 文件 | 内容 |
|---|---|
| `results.jsonl` | 每个声道/片段一行；文件无法解码时保留文件错误行 |
| `results.csv` | 相同记录的扁平列，便于排序与复听定位 |
| `run.json` | 版本、依赖、固定权重哈希/来源/许可、CPU/线程、分段参数、计数和运行时间 |

成功片段包含文件路径、声道、片段编号、相对录音起点的起止秒、采样率、RMS dBFS 和 `scores`。五项分数键为：

```text
mos, noisiness, discontinuity, coloration, loudness
```

第三项是断续、第四项是音色，不能交换。各项都是质量预测值，通常越高越好；不要把噪声分理解为噪声大小，也不要强制裁剪可能越界的模型回归结果。

| 退出码 | NISQA 含义 |
|---|---|
| `0` | download/doctor 检查通过，或 analyze 全部已评分；不证明音质合格 |
| `1` | doctor 有未就绪项，或 analyze 有短段/静音等证据不足且无处理错误 |
| `2` | 参数、缺少/损坏权重、依赖或模型初始化错误；不可当作评分结果 |
| `3` | 有文件/片段处理错误；已成功的记录仍保留，检查产物而非整批重跑覆盖 |

`--json` 放在 `nisqa` 前面，stdout 使用现有单个 JSON 封套；运行库提示可能出现在 stderr。汇总中的 `scored_audio_seconds` 按实际评分声道分别累计，因此双声道可能接近录音墙钟时长的两倍。`rtf_including_load` 包含模型导入/加载及处理，不是纯模型内核运行速度；首次特征编译也可能较慢。

## 本地实战脚本

准备一段经过授权、含有效说话的单声道文件，然后执行：

```bash
python scripts/nisqa_smoke.py data/speech.wav \
  --model-dir .local/nisqa-model --out outputs/nisqa-smoke-001
```

脚本调用安装后的 CLI，要求至少一段真实模型评分、没有处理错误；额外记录启动至完成的墙钟时间、子进程峰值 RSS 和完整 JSON 回执。它不下载权重，不生成或上传用户录音。Mac/Linux 可运行，`resource` RSS 单位已按平台换算。若模型加载或输入错误，仍保留原始 CLI 退出码。

无需权重的测试（从仓库根目录、选定虚拟环境执行；假评分器服务测试仍需 soundfile）：

```bash
env -u VOICE_TOOLS_SIP_LOOPBACK -u VOICE_TOOLS_NISQA_MODEL_DIR -u VOICE_TOOLS_NISQA_AUDIO -u VOICE_TOOLS_TEST_LATENCY_DIR \
  PYTHONPATH="$PWD/src" python -m unittest discover -s tests/nisqa -v
```

真实权重对齐测试（显式指定本地素材，不下载）：

```bash
env -u VOICE_TOOLS_SIP_LOOPBACK -u VOICE_TOOLS_TEST_LATENCY_DIR \
  VOICE_TOOLS_NISQA_MODEL_DIR="$PWD/.local/nisqa-model" \
  VOICE_TOOLS_NISQA_AUDIO="$PWD/data/speech.wav" \
  PYTHONPATH="$PWD/src" python -m unittest tests.nisqa.test_real_model -v
```

对齐测试禁止 HTTP 请求，验证本工具的五项输出与 TorchMetrics 1.9.0 函数输出一致。这验证实现路径，不是人工听感准确度评测。

上述路径需自行准备，未满足条件而跳过不算专项通过。普通命令临时隔离已设置的模型开关；分层运行、源码来源确认及结果复用见[测试指南](testing.md)。

## 资源与部署建议

优先 CPU 单进程、2 线程、小片段。Linux 可先以 4 vCPU/8 GB 内存作为验证预算，不是官方最低配置或吞吐承诺；是否加进程由自己的音频量、处理时窗、内存和吞吐测量决定。M1 Max/32 GiB 的实际执行证据见 [Mac 验收记录](nisqa-validation.md)；2 核/2 GiB 容器的执行记录见 [Linux 验收](nisqa-linux-validation.md)。

Linux 可把隔离环境或容器、已校验权重和 CLI 放在批量任务中；不需要先包装 HTTP 服务。一次 CLI 处理整批文件，在进程中只加载一个模型。多进程会分别加载运行库和权重，不应无限并发。原始录音常比模型更占磁盘，16 kHz/16-bit/单声道 PCM 约 115.2 MB/小时。

如部署后有失败，先看 `nisqa doctor`、退出码及 `results.jsonl` 的 reason，不要静默混音、补零或改分数。已有 `qa` 的无声、应答时序等规则继续独立使用。

## 可选评分溯源与间隙关联

`voice-tools nisqa analyze call.wav --channel right --provenance --out outputs/nisqa` 额外写 provenance.json，记录源音频评分前后的一致性及结果摘要；原 results.jsonl/results.csv 列和分段规则不变。不启用时沿用默认行为。gaps 只读取已完成的分段结果，按同一源录音、声道和区间匹配；旧结果可显式降级为未验证旁证，不产生间隙音质分。见 [关联合同](output-gaps-contract.md)。
