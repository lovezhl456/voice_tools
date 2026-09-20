# 第三方许可清单

本项目自有代码采用 Apache-2.0，完整文本见根目录 `LICENSE`。第三方组件继续适用其原始许可，不因被本项目调用、安装或打包而改为 Apache-2.0。

本清单覆盖仓库内嵌组件、`pyproject.toml` 声明的直接依赖，以及主要可选后端和工具；不是某个已发布镜像全部传递依赖的 SBOM 或商业合规认证。依赖范围可能解析到不同版本，实际交付时应保留所安装版本的许可、版权声明，并履行对应的源码提供等义务。系统软件包、原生库、构建依赖及模型/样本的许可需要随具体产物核对。

## 随源码和安装包分发的组件

| 组件 | 版本 / 范围 | 许可 | 保留方式与来源 |
| --- | --- | --- | --- |
| wavesurfer.js | 7.12.12，含 regions、timeline 插件 | BSD-3-Clause | 完整版权及许可保存在 `src/voice_tools/core/review/vendor/wavesurfer.LICENSE.txt`；同目录 `manifest.json` 记录来源与摘要；[上游](https://github.com/katspaugh/wavesurfer.js/tree/7.12.12) |

wavesurfer.js 的版权归原权利人所有；分发源码或二进制时保留其许可声明，不得擅自使用权利人名称为产品背书。其许可文件保留在 Python 包内，并随发行包的许可资料一同分发。

## Python 直接依赖

以下范围对应 `pyproject.toml`，由安装工具单独获取，并非把这些项目的完整源码纳入本仓库。表中为主体许可；wheel 内附带的原生组件可能具有其他许可。

| 组件 | 声明范围 / 用途 | 主体许可 | 上游 |
| --- | --- | --- | --- |
| NumPy | `>=1.24,<3`，基础依赖 | BSD-3-Clause | [NumPy](https://github.com/numpy/numpy)；二进制中的 BLAS 等组件另有声明 |
| webrtcvad-wheels | `>=2.0.14,<3`，可选 VAD | Python 封装 MIT；WebRTC 原生代码 BSD 类许可 | [webrtcvad-wheels](https://github.com/daanzu/py-webrtcvad-wheels)；保留其附带的 WebRTC 声明 |
| PyTorch (`torch`) | `>=2.6,<3`，可选 NISQA | BSD-3-Clause；附带第三方代码各自授权 | [PyTorch](https://github.com/pytorch/pytorch) |
| TorchMetrics | `==1.9.0`，可选 NISQA | Apache-2.0 | [TorchMetrics](https://github.com/Lightning-AI/torchmetrics/tree/v1.9.0) |
| librosa | `>=0.10.2,<0.12`，可选 NISQA | ISC | [librosa](https://github.com/librosa/librosa) |
| SoundFile | `>=0.12,<0.14`，可选 NISQA | BSD-3-Clause | [SoundFile](https://github.com/bastibe/python-soundfile)；使用的 libsndfile 另受 LGPL 许可约束 |
| Requests | `>=2.31,<3`，可选 NISQA | Apache-2.0 | [Requests](https://github.com/psf/requests) |

## 可选后端、外部工具和构建组件

| 组件 | 使用方式 | 许可及分发边界 |
| --- | --- | --- |
| PJSIP / PJSUA2 | SIP 原生 Python 绑定；统一任务镜像从 pjproject 2.17 编译 | [上游 COPYING](https://github.com/pjsip/pjproject/blob/2.17/COPYING) 及 [2.17 文件头](https://github.com/pjsip/pjproject/blob/2.17/pjlib/include/pj/types.h) 声明 GPL-2.0-or-later；第三方文件及例外以各自声明为准，也可向 [PJSIP](https://www.pjsip.org/licensing.htm) 取得商业许可。Python 绑定涉及链接，不能把它一概视为独立命令调用 |
| ViSQOL | 固定提交 `38d0b0163e441047d4429bf07ad09e5b9031d02c`，单独安装或构建后端 | [Apache-2.0](https://github.com/google/visqol/blob/38d0b0163e441047d4429bf07ad09e5b9031d02c/LICENSE)；原生构建依赖、模型及演示素材按各自声明核对，不能仅凭 ViSQOL 主体许可推断全部产物 |
| FFmpeg / FFprobe | 可选音频处理命令；部分镜像安装 | [LGPL-2.1-or-later 为基础，具体构建可能启用 GPL 或 nonfree 组件](https://ffmpeg.org/legal.html)；以实际二进制构建配置与许可为准 |
| Wireshark 工具（tshark、dumpcap、mergecap） | 抓包与媒体分析命令 | [GPL-2.0-or-later](https://www.wireshark.org/docs/wsug_html_chunked/ChapterIntroduction.html)；镜像中实际安装的组件仍需履行对应分发义务 |
| SIPp | 可选 SIP 回放和压力测试命令 | [GPL-2.0-or-later](https://github.com/SIPp/sipp)；具体版本附带的库/例外另行核对 |
| sngrep | 用户单独安装的辅助排障工具 | [GPL-3.0-or-later](https://github.com/irontec/sngrep)；本项目不内嵌其源码 |
| OpenSSH、Python 基础镜像及系统软件包 | SSH 采集、运行与构建环境 | 各软件包独立许可；保留镜像中的 copyright / license 资料并核对实际软件包清单 |
| Bazel、TensorFlow、protobuf、Armadillo、libsvm 等 | ViSQOL 安装脚本获取的构建依赖 | 各自许可；版本与下载来源见 `scripts/install_visqol.py`，本清单不替代这些上游许可文件 |

**PJSIP 组合分发说明：** 本项目自有代码的 Apache-2.0 授权不授予闭源分发 PJSIP 的权利，也不表示包含 PJSUA2 的完整程序或 Docker 镜像可以仅按 Apache-2.0 分发。PJSIP 2.17 已核对的文件头允许 GPLv2 或后续版本；Apache-2.0 与 GPLv3 兼容，与 GPL-2.0-only 不兼容。包含该绑定的组合程序应评估按 GPLv3 条件分发并履行相应源码义务，或取得适当商业许可，同时核对所有其他组成部分。外部命令的独立聚合与库链接应分别判断。本次加入清单不代表统一镜像已经完成再分发许可审计。

## NISQA 官方模型权重：非商业限制

- 原 NISQA 项目代码采用 MIT；本项目通过 TorchMetrics 调用模型。代码许可与模型权重许可是两回事。
- 官方 `nisqa.tar` 权重采用 **CC BY-NC-SA 4.0**，含署名、非商业及相同方式共享条件。固定来源：[`fe84f0f…/weights/LICENSE_model_weights`](https://github.com/gabrielmittag/NISQA/blob/fe84f0f252abec382b24367d5b22498a7ce34dbb/weights/LICENSE_model_weights)；[CC BY-NC-SA 4.0 条款](https://creativecommons.org/licenses/by-nc-sa/4.0/)。
- 权重不随本项目源码、wheel、源码发行包或 Docker 镜像分发；由用户显式执行 `voice-tools nisqa download` 下载到本地缓存。
- **商业客服生产系统、收费服务或其他商业用途需另行解决权重授权。** 免费提供某项功能、仅在公司内部使用、更换封装或采用 Apache-2.0 均不会自动豁免非商业限制；应按具体用途及权利人授权判断。
- 再分发或修改权重时须按原条款保留署名和许可、标明修改，并遵守非商业及适用的相同方式共享要求。本项目不对上游权重作额外授权，也不据此把所有评分输出一概认定为 CC BY-NC-SA。

## 维护与交付

增删、升级、复制第三方组件或改变镜像打包方式时同步更新清单。分发本项目时保留根目录 `LICENSE`、本清单及内嵌 wavesurfer.js 许可；分发依赖、模型或镜像时另外保留其对应许可和版权资料，并履行实际适用的义务。仅提供一份许可清单或上游链接不能替代 GPL 等协议要求的对应源码交付。

## hiccup 问题定义参考

参考 https://github.com/AhmadIbrahiim/hiccup/tree/260c92e867203f192dd8d5c37879b4e624ec3f11 对输出中途停顿的分类。gaps 独立实现；未复制其源码、测试或页面，不作为运行依赖。新增自有实现沿用本仓库 Apache-2.0；共享 WaveSurfer 资源及 BSD 许可证位于 `src/voice_tools/core/review/vendor/`。
