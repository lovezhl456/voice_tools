# ViSQOL 本机实战验收 · 2026-09-20

[安装与使用](visqol.md) · [结构化验收摘要](visqol-local-validation.json)

本轮已在 Mac 本机完成源码下载、原生编译、单对/批量评分、失败处理和独立 wheel 安装验证。最终 CLI 版本 **0.11.1**，已同步主线 NISQA、SIP 批量功能。这里记录实际结果，不把部署建议当成已测容量。

## 环境与固定版本

| 项目 | 实际环境 |
|---|---|
| 主机 | Apple M1 Max，10 逻辑 CPU，32 GiB RAM |
| 系统 / 编译器 | macOS 26.6.2，Xcode 26.6（17F113） |
| Python / NumPy | 3.9.6 / 2.0.2，独立虚拟环境 |
| FFmpeg | 8.1.2；仅用于演示的显式重采样 |
| ViSQOL | `38d0b0163e441047d4429bf07ad09e5b9031d02c` |
| Bazel | 5.1.0，Darwin arm64 |
| 构建并发 | `--jobs 4 --memory-mb 4096`；内存参数是调度预算，不是峰值实测 |

源码归档、Bazel、最终二进制、模型和安装包的 SHA256 见配套 JSON。安装生成的 `installation.json` 还记录实际命令、完整日志位置及依赖补丁。未修改 ViSQOL 自身评分代码或模型。

## 安装中实际解决的问题

1. 旧依赖镜像失效、Bazel Java 下载器限流：安装器用 curl 从官方服务预取同版本归档，核对固定 SHA256，供 `--distdir` 使用。
2. macOS 26 拒绝缺少 `LC_UUID` 的旧 Xcode 包装器：使用 `BAZEL_USE_CPP_ONLY_TOOLCHAIN=1`。
3. 旧 zlib 的 Classic Mac 分支与现代 SDK 冲突：增加针对目标及宿主编译的 `fdopen=fdopen` 定义。
4. TensorFlow Lite 2.11 的 `std::abs<float>` 无法在当前 libc++ 编译：只对已核对 SHA256 的 `elementwise.cc` 应用等价 lambda 补丁；文件有未知修改时拒绝处理。补丁前后校验值均留档。

这些处理已进入 [install_visqol.py](../scripts/install_visqol.py)。最终构建复用了排障中产生的下载与编译缓存；没有把最后一次增量构建的 38 秒当成首次完整安装耗时，也没有测量完整编译过程的峰值内存。Linux、Intel Mac 和全新空缓存安装未在本轮独立复验。

## 真实音频评分

[演示脚本](../scripts/visqol_demo.py)从固定官方源码取 `CA01_01.wav` 和 `transcoded_CA01_01.wav`。原样本为单声道 48 kHz，约 **2.738 秒**。语音模式先经 FFmpeg 转为 16 kHz；每条命令和输入校验值保留在 `demo.json` 与对应结果目录。

| 输入配对 | 模式 | 实际 MOS-LQO |
|---|---|---:|
| 原声与自身，显式转 16 kHz | speech / lattice | 4.506983757019043 |
| 原声与官方转码版本，显式转 16 kHz | speech / lattice | 3.3159115314483643 |
| 原声与人工加噪版本，16 kHz | speech / lattice | 1.0 |
| 原始 48 kHz 原声与官方转码版本 | audio / SVR | 1.7658378752958073 |

人工噪声使用固定种子 `20260920`、标准差 `0.08`，只是演示对照。它不是实采故障。默认 lattice 模型的同文件结果并不保证为 5；不同模型的分数也不能直接混排。

audio 结果与上游 `kCA01_01AsAudio = 1.7658378752958486` 的差约 `4.13e-14`，小于上游容差 `0.0001`。语音重采样结果不直接硬比上游用原始 48 kHz 样本产生的 speech 基准。

另外使用真实后端执行“一条有效配对 + 一条缺失文件”的混合批次：完成 1、失败 1，退出码 **3**；成功分数保留，失败项 `moslqo=null`。这与仅用模拟程序测试协议是两种不同的证据。

## 官方基准测试

固定上游源码的 **20 项 conformance 测试、3 项 TFLite quality mapper 测试全部通过**。覆盖语音 lattice / exponential、同文件、一般音频损伤和对齐样本；一致性测试使用上游自己的预期分数与 `0.0001` 容差。

默认测试动态链接在本机出现 `_TfLiteXNNPackDelegateDelete` 缺失，尚未进入断言；改用 `--dynamic_mode=off` 静态链接后，两组测试完整通过。该测试配置不改变评分算法，也没有修改测试预期值。开发者可在已安装的虚拟环境中按以下方式复验；这里只展示本次通过的 macOS 参数：

```bash
VISQOL_PREFIX="$PWD/.local/visqol"
VISQOL_PYTHON="$(command -v python)"
cd "$VISQOL_PREFIX/source"
BAZEL_USE_CPP_ONLY_TOOLCHAIN=1 PYTHON_BIN_PATH="$VISQOL_PYTHON" \
  "$VISQOL_PREFIX/bin/bazel" --output_user_root="$VISQOL_PREFIX/bazel-cache" --batch \
  test :conformance_test :tflite_quality_mapper_test -c opt \
  --jobs=4 --local_ram_resources=4096 --nokeep_going \
  --distdir="$VISQOL_PREFIX/downloads" \
  '--per_file_copt=external/zlib/.*@-Dfdopen=fdopen' --host_copt=-Dfdopen=fdopen \
  --dynamic_mode=off --test_sharding_strategy=disabled --test_output=all
```

`VISQOL_PREFIX` 和 `--distdir` 换成实际安装前缀与下载缓存。此命令是额外的开发者验收，会生成测试产物，日常评分不需要运行。

## 实测资源

对官方转码样本，每种模式启动三个新的**原生评分进程**，使用 macOS `/usr/bin/time -l` 测量；操作系统文件缓存已预热，未清缓存，不声称是冷启动。内存是单个原生进程 RSS，不包含 Python 调用层、系统或并发总占用。

| 模式 | 输入时长 | 3 次墙钟耗时 | 3 次峰值 RSS |
|---|---:|---|---|
| speech，16 kHz | 约 2.738 秒 | 0.54 / 0.53 / 0.53 秒 | 50.56 / 54.36 / 51.97 MiB |
| audio，48 kHz | 约 2.738 秒 | 1.99 / 1.97 / 2.00 秒 | 67.84 / 63.05 / 64.75 MiB |

| 磁盘项 | 实测 |
|---|---:|
| 原生可执行文件 | 7,995,464 字节，约 7.63 MiB |
| 本次 speech 模型 | 2,233,840 字节，约 2.13 MiB |
| 本次 audio 模型 | 138,117 字节，约 0.132 MiB |
| 完整源码目录，不追踪 Bazel 输出链接 | 约 115 MiB |
| 源码、下载缓存、Bazel 程序及构建/测试缓存 | 约 3.7 GiB；会随构建和测试变化 |

`bazel-bin` 指向构建缓存。不要仅复制该链接或删除它指向的缓存，然后假定安装仍可运行。当前安装器保留完整源码和缓存，不提供已验收的裁剪部署包。

用于选机器的**工程预算**：少量短语音、单进程评分可先以 2 CPU / 2–4 GiB RAM 试验；本机编译建议 4 CPU、8–16 GiB RAM、预留 10–20 GiB 磁盘。它们不是官方最低要求或本轮验证的最小配置。吞吐和并发应使用目标机器、目标录音重新测试。

## CLI 与回归检查

- 下载、编译和 `doctor` 文件检查完成；随后实际通过 `voice-tools visqol score / batch` 评分。
- 最终 0.11.1 wheel 在仓库外的独立环境离线安装，包元数据、CLI、schema 一致；发现 NISQA、ViSQOL、SIP batch，并实际评分得到 `3.3159115314483643`。
- 17 项 ViSQOL 专项测试通过：非法/静音/截断音频、相对 CSV 路径、逗号/中文/空格路径、混合失败、超时、后端异常分数、产物隔离，以及安装器校验与文件保护等。这些协议测试使用模拟后端，真实评分证据见上文。
- 同步主线后，全仓 **354 项，322 项执行通过、32 项可选测试跳过**。跳过：19 项 PJSUA2 回环测试、1 项 VAD、11 项需要 soundfile 的 NISQA 服务测试、1 项 NISQA 真实权重测试。本轮未安装这些可选功能以扩大验收范围。
- 首次沙箱回归中的两处回环 HTTP 端口权限错误，已通过允许本机端口的完整重跑排除；不把该失败日志当成通过记录。
- 最终检查又补充了“CSV 表头引号未闭合”的结构化报错，并重跑全部 17 项 ViSQOL 专项；最终 wheel 重新构建、离线安装及真实评分通过。全仓数字对应这项小修复前的完整回归，专项和 wheel 验证对应最终代码。

## 边界

没有完成真实中文通话、生产网关、长录音质量准确度、并发压力或 Linux 部署验收。ViSQOL 仍需要同一句对应参考音频；脚本无法证明两份录音语义匹配。测试成功说明当前接入和所测样本可复现，不建立通用告警阈值。

仓库只提交代码、文档和精简数值摘要；原始音频、模型、构建缓存、完整日志保留在本机，不进入 Git。

## 上游依据

- [固定版本一致性测试](https://github.com/google/visqol/blob/38d0b0163e441047d4429bf07ad09e5b9031d02c/tests/conformance_test.cc)
- [固定版本基准常量](https://github.com/google/visqol/blob/38d0b0163e441047d4429bf07ad09e5b9031d02c/src/include/conformance.h)
- [TFLite 映射器测试](https://github.com/google/visqol/blob/38d0b0163e441047d4429bf07ad09e5b9031d02c/tests/tflite_quality_mapper_test.cc)
