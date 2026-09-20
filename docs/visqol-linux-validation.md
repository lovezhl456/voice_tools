# ViSQOL Linux / Docker 实战验收 · 2026-09-20

[Docker 下载、安装与使用](visqol-docker.md) · [结构化结果与校验值](visqol-linux-validation.json) · [此前 Mac 验收](visqol-local-validation.md)

**已用本地 Docker 跑通 Linux 源码编译、真实 CLI 评分和独立运行镜像。** 最终版本为 `0.11.1`。本次在 M1 Mac 上通过 QEMU 运行 Ubuntu / amd64，下面的耗时不能当作原生 x86 Linux 服务器性能。

## 环境与验证范围

| 项目 | 本次配置 |
|---|---|
| 宿主机 | M1 Max，10 逻辑 CPU，32 GiB RAM |
| Docker | Desktop 4.19.0 / Engine 23.0.5；VM 为 arm64、5 CPU、约 7.67 GiB |
| 容器 | Ubuntu 22.04.5 LTS，linux/amd64，跨架构 QEMU 仿真 |
| 工具链 | GCC 11.4.0，Python 3.10.12，NumPy 2.2.6，FFmpeg 4.4.2 |
| ViSQOL | 固定提交 `38d0b0163e441047d4429bf07ad09e5b9031d02c`，Bazel 5.1.0 |
| 编译限制 | 4 CPU / 6 GiB，内存加 swap 上限同为 6 GiB；Bazel 4 路、4096 MB 调度预算 |
| 评分限制 | 2 CPU / 2 GiB、禁网、只读根文件系统；工具镜像的后端 volume 只读 |

未修改 ViSQOL 算法或模型；Linux 未使用 Mac 的 TFLite libc++ 补丁。程序、两个模型、源码归档、官方 Bazel 和兼容层的 SHA256 均记录在配套 JSON。

## 下载与编译中实际处理的问题

首次启动官方 Bazel 时，旧 Docker / QEMU 报错：

```text
Opening zip "/proc/self/exe": lseek(): Bad file descriptor
Failed to open '/proc/self/exe' as a zip file
```

该次退出码为 36，没有 OOM。安装器新增显式 `--qemu-proc-self-workaround`：只有匹配此 Linux 启动错误、且官方 Bazel SHA256 正确时，才编译一个很小的文件打开兼容库，将启动器对 `/proc/self/exe` 的打开重定向到原文件。官方 Bazel 二进制保持不变。

库初始化后清除 `LD_PRELOAD` 与 `VISQOL_BAZEL_SELF_EXE`；已检查构建时的 Java 子进程环境，两个变量均未继承。兼容库、C 源码和启动脚本的校验值留档。正常启动的 Linux 主机不会启用这一处理，其他启动错误也不会被它掩盖。

成功构建阶段耗时 **2197.50 秒，约 36 分 37 秒**；该次安装器进程总耗时 **37 分 17.77 秒**，容器退出 0、未 OOM。复用了已校验的下载缓存；此前镜像构建、下载与排障不计入这两个数字，不能称为从空机器开始的总安装耗时。没有测得整个编译容器的峰值总内存，6 GiB 是本次硬限制。

## 真实 CLI 结果

官方 `CA01_01.wav` 和转码样本约 **2.738 秒**。语音演示经容器内 FFmpeg 显式转为单声道 PCM16 / 16 kHz；一般音频模式使用原始 48 kHz 输入。

| 配对 | 模式 | MOS-LQO |
|---|---|---:|
| 原声与自身，16 kHz | speech / lattice | 4.507157802581787 |
| 原声与官方转码版本，16 kHz | speech / lattice | 3.316093921661377 |
| 原声与固定种子人工加噪版本 | speech / lattice | 1.0 |
| 原始 48 kHz 原声与转码版本 | audio / SVR | 1.765837875295841 |

人工噪声是演示对照，不是实采故障。Mac 与 Linux 的 FFmpeg 版本、重采样后文件 SHA256 不同，因此不拿两边的 16 kHz 演示分数做严格一致性判断。

另外直接用同一份上游原始 48 kHz 文件核验固定基准：

| 上游案例 | 预期分数 | 实际分数 | 容差 |
|---|---:|---:|---:|
| speech 转码 | 3.3129234313964844 | 3.3129289150238037 | 0.0001，通过 |
| speech 同文件 | 4.505550384521484 | 4.505544185638428 | 0.0001，通过 |
| audio 转码 | 1.7658378752958486 | 1.765837875295841 | 0.0001，通过 |

这里的原始 48 kHz speech 基准用于核对上游程序；业务 CLI 的 speech 输入要求仍为 16 kHz。Linux 本轮执行三个短样本基准，没有执行完整上游测试套件；此前 Mac 的 20 项 conformance 和 3 项 TFLite 通过记录不充当 Linux 证据。

真实混合 CSV 故意将缺失文件放在第一行、有效配对放在第二行：完成 1、失败 1，退出码 **3**；失败分数为 `null`，第二行保留 `3.316093921661377`。结果目录包含中文和逗号，实际运行成功。

## 独立运行镜像验收

使用 [导出脚本](../scripts/visqol_export_runtime.py)将二进制与两个模型复制为普通文件，核对复制前后 SHA256，保留许可证和安装来源。随后构建 [Dockerfile.runtime](../docker/visqol/Dockerfile.runtime)。

`voice-tools-visqol-runtime:0.11.1-amd64` 已在**不挂载任何后端 volume**的情况下完成：

- `doctor`、speech 单对、speech 两对批量、audio 单对，全部退出 0；分数与工具镜像一致。
- 非 root 用户 `501:20`、禁网、只读根文件系统、临时 `/tmp`、2 CPU / 2 GiB 条件下评分，结果成功写回宿主绑定目录。
- 二进制和模型校验值一致；没有 GCC、Bazel、FFmpeg 或构建缓存，二进制不是符号链接。

此镜像适合输入已准备为正确 WAV 的日常评分。样本演示和格式转换仍使用工具镜像。导出不等于跨架构转换；本次仅证明 Ubuntu / amd64 镜像内可运行。

## 资源实测与部署预算

每种模式启动三个新的 Linux 评分进程，操作系统文件缓存已预热；使用 GNU time，RSS 单位从 KiB 换算为 MiB。下面不包含 Python 调用层、Docker VM 或整机总内存，且有 QEMU 仿真开销。

| 模式 / 约 2.738 秒输入 | 三次墙钟耗时 | 三次进程峰值 RSS |
|---|---|---|
| speech / 16 kHz | 6.60 / 6.62 / 6.63 秒 | 64.35 / 64.24 / 64.92 MiB |
| audio / 48 kHz | 22.57 / 22.59 / 22.83 秒 | 67.44 / 67.54 / 67.79 MiB |

| 磁盘项 | 实测 |
|---|---:|
| 工具镜像，含编译工具 / FFmpeg / Python | 926,646,666 字节，约 883.72 MiB |
| 独立运行镜像，含程序 / 两个模型 / Python | 202,801,537 字节，约 193.41 MiB |
| 单独 ViSQOL 二进制 | 7,095,536 字节，约 6.77 MiB |
| 导出的后端文件总大小 | 9,481,512 字节，约 9.04 MiB |
| 源码及 Bazel 构建 volume | 3,086,884 KiB，约 2.94 GiB |
| volume 外的下载归档缓存 | 183,876 KiB，约 179.57 MiB |

镜像使用 Docker inspect 的未压缩逻辑大小，共享层不能简单相加作为额外磁盘占用；表格不包含 Docker builder 其他缓存。导出目录体积小，不代表系统、Python 和动态库也只有这么大。

**选机器的工程预算**：少量短片段、单进程评分可从 2 CPU / 2 GiB 开始，按实际音频重新量；本地源码编译可参考本次 4 CPU / 6 GiB 配置并预留 10–20 GiB 磁盘。预算和本次限制都不是官方最低配置。无需 GPU，安装后评分不需要网络或 API Key。原生 Linux 的速度、并发容量仍须在目标机器实测。

## 检查与边界

- Mac 和 Linux 的最终 **23 项 ViSQOL Python 专项测试分别全部通过**；Linux 用时 42.029 秒。这些模拟后端和文件保护测试与上面的真实评分分开记录。
- 源码安装、真实评分、部分失败、中文/逗号路径、导出校验及独立镜像均有本机日志与结果；精简数值入 Git，音频、模型、缓存和完整日志不入 Git。
- 未完成原生 x86 Linux 主机或 Linux arm64 验收，也未验证真实中文通话准确度、生产网关、长音频或并发压力；不建立通用告警阈值。

复验步骤见 [Docker 指南](visqol-docker.md)，自动验收入口为 [visqol_linux_smoke.py](../scripts/visqol_linux_smoke.py)。分数仍只说明同一句参考/待测音频在当前配置下的算法估计，不能自动证明输入语义匹配或定位故障原因。

## 上游基准来源

- [固定版本基准常量](https://github.com/google/visqol/blob/38d0b0163e441047d4429bf07ad09e5b9031d02c/src/include/conformance.h)
- [固定版本一致性测试](https://github.com/google/visqol/blob/38d0b0163e441047d4429bf07ad09e5b9031d02c/tests/conformance_test.cc)
