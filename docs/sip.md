# 轻量 SIP 自动拨测：使用手册

使用 `voice-tools sip` 或独立命令 `voice-sip`，从本地脚本发起 SIP UDP 呼叫，播放录音、发送按键并持续录音。执行端采用 PJSUA2，不需要部署 FreeSWITCH。OpenSIPS／FreeSWITCH 可以作为被测网关。

面向大模型的字段、调用顺序和结果解释见 [SIP Agent 协议](sip-ai.md)。命令发现使用 `voice-tools schema --tool sip`。本次实现和真实本地回环的验证范围见 [验证记录](sip-validation.md)。

PR #7 合并后的 [深度检查与修复](sip-deep-review.md) 补充了媒体端口切换、SIGTERM、异常清理和损坏产物的回归。通话中的媒体连接更新会复用同一个接收 WAV；Ctrl-C／SIGTERM 会终止本次原生子进程并保留结果。不可捕获的 SIGKILL 不在此保证内。

公共测试号码和本地应答端的选型见 [测试服务调研](sip-test-targets.md)。已完成 [分层互通验收](sip-interop.md)：20 个端到端用例及 6 个 Baresip 官方自测通过；包含 pjsua、Baresip 和 4 通公共测试服务实际呼叫。[仓库内 HTML 用例摘要](sip-cases/index.html)。

## 1. 第一版能做什么

- 每次命令执行一通呼叫；JSON 顺序策略支持等待、播放文件、DTMF、按媒体时间表播放与挂断。
- SIP UDP、Digest 认证，可选 REGISTER 和出站代理；首批媒体支持 PCMA／PCMU。
- 无需麦克风或扬声器；使用 PJSUA2 的 null audio device 驱动媒体时钟。
- 从接通且媒体可用时开始持续录制接收音频；`record_early=true` 时从提前媒体开始录音。
- PCAP／PCAPNG 中显式选定单方向、单 SSRC 的 RTP；G.711 转 PCM16 WAV，RFC 4733 转去重后的按键事件。
- 需要直接发送原 RTP 载荷时，用 SIPp 单独执行该场景；不与 PJSUA2 同时争用同一通话。

暂不包含 ASR、VAD 条件分支、TTS、自动重拨、批量并发、SRTP 解密、任意 codec 的 PCAP 解码。SIPp 路径第一版只生成无注册、无 Digest 的 IPv4 G.711 单通话场景。复杂代理认证应使用 PJSUA2 路径。

## 2. 安装与依赖

```bash
source .venv/bin/activate
python -m pip install -e .
voice-sip --help
voice-sip doctor
```

| 依赖 | 用途 | 是否影响其他工具 |
|---|---|---|
| `pjsua2` 原生 Python 绑定 | 真正发起呼叫 | 可选；未安装仍可查看帮助、校验、预演与准备素材 |
| `tshark` | PCAP／PCAPNG 解析、筛选和可选抓包 | 仅 PCAP 相关命令需要 |
| 启用 PCAP play 的 `sipp` | 直接 RTP 回放 | 仅 `sipp-run` 需要 |

`pip install -e .` 不会自动下载 SIP 库、系统工具或模型。PJSUA2 不是本项目的纯 Python 依赖；必须针对正在使用的 Python 构建。

本次在本机项目 `.venv` 内构建并验证 PJSIP **2.17**；构建文件留在忽略目录 `.local/sip-build/`。此路径是本机验证环境，未作为可移植二进制提交。换机器后先运行 `doctor`。

从官方源码构建的参考步骤（需编译器、make；在虚拟环境内安装构建工具）：

```bash
python -m pip install swig setuptools wheel
curl -L --fail -o pjproject-2.17.tar.gz \
  https://github.com/pjsip/pjproject/archive/refs/tags/2.17.tar.gz
tar -xzf pjproject-2.17.tar.gz
cd pjproject-2.17
CFLAGS=-fPIC CXXFLAGS=-fPIC ./configure --disable-video --disable-sound
make dep
make -j4
cd pjsip-apps/src/swig/python
make wheel PYTHON_EXE="$(command -v python)"
python -m pip install dist/pjsua2-*.whl
```

macOS 使用单架构依赖时，可在构建 wheel 前设置 `export ARCHFLAGS="-arch $(uname -m)"`，不要把单架构底库误标成完整 universal2。Linux 不需要这个 macOS 编译参数。官方说明：[PJSIP 构建入口](https://docs.pjsip.org/en/latest/get-started/index.html)。

SIPp 请使用官方 release 的源码包或完整 Git 标签检出；GitHub 自动 Source code 归档可能只有占位 `version.h`。构建启用 `-DUSE_PCAP=1`，先用 `sipp -v` 核对。不同系统的 PCAP 发包或 live capture 可能要求额外权限；本工具不会自动 sudo、切换网关或修改网络配置。

## 3. 创建、校验和预演策略

```bash
voice-sip init --out outputs/sip-scenario-001
voice-sip validate outputs/sip-scenario-001/scenario.json
voice-sip run outputs/sip-scenario-001/scenario.json \
  --dry-run --out outputs/sip-plan-001
```

以上均不发 SIP。模板地址 `example.invalid` 是占位符，实际执行会拒绝它。输出目录必须不存在或为空，工具不会清空已有结果。

策略示例：

```json
{
  "schema_version": "1.0",
  "target_uri": "sip:1001@192.0.2.10:5060",
  "account": {"id_uri": "sip:tester@192.0.2.20"},
  "network": {"bind_address": "192.0.2.20", "sip_port": 5062, "rtp_port": 4000},
  "codec": "PCMA",
  "connect_timeout_s": 30,
  "max_call_s": 120,
  "record_early": false,
  "steps": [
    {"action": "wait", "seconds": 2},
    {"action": "dtmf", "digits": "1#", "method": "rfc4733", "duration_ms": 160, "gap_ms": 100},
    {"action": "play", "file": "question.wav"},
    {"action": "wait", "seconds": 5},
    {"action": "hangup"}
  ]
}
```

`192.0.2.*` 为文档地址，需替换为实际环境。相对音频路径以策略文件所在目录为基准。播放文件要求 8 kHz、单声道 PCM16 WAV；不会自动混音或偷偷转码。

生成不含真人语音的测试素材：

```bash
python examples/sip/generate_demo.py --out outputs/sip-demo-001
voice-sip validate outputs/sip-demo-001/scenario.json
```

需要认证时，在 `account` 增加环境变量引用：

```json
{
  "id_uri": "sip:tester@example.net",
  "registrar_uri": "sip:192.0.2.10:5060",
  "auth": {"username": "tester", "realm": "*", "password_env": "SIP_TEST_PASSWORD"}
}
```

不需要 REGISTER 时省略 `registrar_uri`；INVITE 的 401／407 仍可使用 `auth`。需要代理时增加 `proxy_uri`。密码在运行环境中提供，JSON 不接受 `password` 字段。`validate`／`--dry-run` 不要求密码已存在；真正执行时检查。

`bind_address` 是本机实际 IPv4，`public_address` 可指定 SIP 和 RTP 对外公布的 IPv4；这些参数不建立 NAT 映射或放通防火墙。SIP 网关与 RTP 端点可能不同，以 SDP 为准。

## 4. 执行与查看录音

在有真实授权测试目标的配置文件上执行：

```bash
voice-sip run path/to/scenario.json --out outputs/sip-call-001
# 等价的机器接口
voice-tools --json sip run path/to/scenario.json --out outputs/sip-call-002
```

| 产物 | 含义 |
|---|---|
| `plan.json` | 展开路径与默认值后的执行计划、素材摘要；无密码值 |
| `rx.wav` | 本端实际收到并经 PJSUA2 解码／抖动缓冲处理的对端音频 |
| `tx_source.wav` | 根据实际播放开始／停止事件和源文件重建的本地发送源时间轴 |
| `events.jsonl` | SIP 状态、录音起点、动作开始／完成、按键、挂断等事件 |
| `result.json` | 执行状态、SIP Call-ID、SIP 状态码、codec、完成步骤及错误 |
| `native.log` | 原生进程输出；常规 SIP 报文日志关闭，避免污染机器 JSON |
| `sources/` | 实际执行前按摘要保存的播放素材快照，避免原文件被修改后产生不一致 |

**`tx_source.wav` 不是真实出站 RTP 录音，也不能证明对端收到声音。** 两个 WAV 的对齐是调度时间估计，不承诺逐采样一致。带外 DTMF 保存在事件中，不合成人工按键音混入 WAV。不要直接把这两个文件称为采样同步的真实双轨录音，也不要用复制单声道的方式补轨。

录音与播放同时进行；失败和中断仍尽量保留现有录音及事件。正常成功表示既定动作完成，`business_assertions=not_evaluated` 表示未判断 IVR／机器人回答是否正确。

退出码：`0` 完成／离线计划成功；`2` 输入、配置或运行所需依赖有问题；`3` 呼叫／回放／录音未完整完成，或 `doctor` 发现 PJSUA2 不可用。先看 `result.json` 的错误和最后完成步骤。不要因 SIP 200 OK 就认定业务成功。

## 5. PCAP → 音频＋按键事件

先列出流，不猜测方向：

```bash
voice-sip pcap-inspect input.pcapng --rtp-port 4000
voice-sip pcap-import input.pcapng --rtp-port 4000 \
  --stream RTP_STREAM_ID --dtmf-pt 101 --out outputs/sip-media-001
```

把 `RTP_STREAM_ID` 替换为 inspect 返回的 `rtp-…`。`--dtmf-pt 101` 只是示例，必须按原 SDP 的 telephone-event 映射填写；不自动假定 101。静态 PT 0／8 可推断 PCMU／PCMA，动态 G.711 必须同时提供 `--audio-pt` 和 `--codec`。

有 SIP/SDP 的抓包通常能自动识别 RTP。只有裸 RTP 时用实际端口 `--rtp-port`，可重复；不要把 SIP 端口也强制 Decode As RTP。

产物：`audio.wav`、`dtmf.json`、`media.json`、可编辑的 `scenario.json`。修改新策略的目标和网络参数，再校验并执行。`play_media` 同时播放整段音频，并按事件相对时间发送 DTMF；普通 `play` 不读取按键事件。

导入规则：仅选测试主叫原来发出的方向；重复的 telephone-event 更新／结束包合并为一次按键；音频按 RTP 时间戳重建，缺失采样填零并报告数量。时间零点是所选流的最早 RTP 时间戳，不是接通时刻。丢失、乱序和抖动不会被当成原网络行为精确复现。

首版不跨 SSRC 合并，不自动推断动态 codec，不解密 SRTP，不提取 SIP INFO。遇到其他 payload type、冲突重叠、截断包或超限直接拒绝。抓包发现 SIP INFO 会提示未转换；按键属于该通话时需人工加入策略。in-band DTMF 本来就在音频内，不要再次添加同一个按键事件。

## 6. SIPp 直接 RTP 回放

先离线准备独立回放包：

```bash
voice-sip sipp-prepare input.pcap --rtp-port 4000 --stream RTP_STREAM_ID \
  --dtmf-pt 101 --target sip:1001@192.0.2.10:5060 \
  --local-ip 192.0.2.20 --sip-port 5062 --rtp-port-local 6000 \
  --out outputs/sipp-package-001
voice-sip sipp-run outputs/sipp-package-001 --dry-run --out outputs/sipp-plan-001
```

回放包包含筛选后的 `selected.pcap`、`scenario.xml`、带摘要的 `plan.json`。不会把整份双向 PCAP 直接发给对端。对端须接受原包使用的 codec 和 telephone-event PT；不转换 RTP 编码或载荷映射。

实际执行和可选录包：

```bash
voice-sip sipp-run outputs/sipp-package-001 --out outputs/sipp-run-001
# 本地回环举例；Linux 通常为 lo，macOS 为 lo0
voice-sip sipp-run outputs/sipp-package-001 --capture-interface lo0 \
  --out outputs/sipp-run-002
```

可通过 `--sipp /absolute/path/to/sipp` 使用非 PATH 中的程序。一次只拨一通，保存 `sipp.log` 和退出状态；启用抓包时另有 `media.pcapng`／`capture.log`。抓包无法启动则不发起呼叫。

**SIPp 路径不直接生成接收 WAV。** 需要录制原始媒体证据时显式选择 `--capture-interface`；之后 inspect 选中接收流，可用 `pcap-import` 解码 G.711。后处理 WAV 是抓包重建，与 PJSUA2 接收端的解码／抖动缓冲录音含义不同。

## 7. 本地回环验收与维护

仓库提供独立的最小 SIP/RTP 应答端，只绑定 `127.0.0.1`，不会访问外部网关：

```bash
python -m unittest discover -s tests/sip -v
VOICE_TOOLS_SIP_LOOPBACK=1 python -m unittest tests.sip.test_loopback -v
```

默认单元测试跳过需要网络和原生库的回环项。显式开启后实际检查 UDP 媒体、按键、录音、Digest、提前媒体、忙线、对端挂断和导入 PCAP 的按键去重；不是只 mock SDK。结果不替代真实网关／NAT／运营商互通验收。

修改动作或字段时同步更新 [Agent 协议](sip-ai.md)、示例、测试，并从实际 CLI 重新生成 `docs/cli-schema.json`。保持工具边界：`tools/sip` 可以使用公共 `core`，不直接导入其他工具。
