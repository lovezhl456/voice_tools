# 会话抓包与联合报告

多主机、多通话的定时抓包、会话检索、FS 周期快照和 HOMER 联动见[批量会话指南](batch-sessions-homer.md)。本页保留单个 UUID 的操作说明；持续留存、ESL 和一键编排见 [2.0 指南](capture-v2.md)。

`voice-tools capture` 通过 SSH 在指定 Linux 主机上查询活动 FreeSWITCH 通话，按端点运行 dumpcap（可选旧 tcpdump 后端），再用 SCP 取回 PCAP。`voice-tools report` 在本机汇总一份或多份录音、PCAP，输出离线 HTML 和 JSON。两者不需要 GPU 或在线模型。

## 1. 按 UUID 抓包

本机需要 OpenSSH 的 `ssh`、`scp`。远端需要 `fs_cli`、`dumpcap`（旧后端为 `tcpdump`）、GNU `timeout`、`sha256sum` 和标准 shell 工具。SSH 用户须能调用本机 FreeSWITCH ESL API，并具备抓包权限；`fs_cli` 使用远端现有配置，不通过 CLI 传入 ESL 密码。`--fs-cli /usr/local/freeswitch/bin/fs_cli` 可指定安装路径。

SSH 使用密钥或 agent，强制 `BatchMode=yes` 和 `StrictHostKeyChecking=yes`。先按你的运维流程配置 SSH 别名和已核实的 known_hosts。工具不会关闭主机身份校验，不收集 SSH 密码，也不修改服务器配置。IPv6 主机请通过 SSH 别名访问。

```bash
# 只读查询：仍会 SSH 调用 fs_cli，但不会创建远端目录或启动抓包
voice-tools capture start --host fs-prod \
  --uuid 11111111-2222-3333-4444-555555555555 \
  --seconds 60 --interface any --dry-run --out outputs/call-plan

# 活动通话期间执行。每次使用新的输出目录。
voice-tools capture start --host fs-prod \
  --uuid 11111111-2222-3333-4444-555555555555 \
  --seconds 60 --max-mib 64 --sudo --out outputs/call-capture
```

默认读取 `uuid_dump UUID` 的 `local_media_ip`、`local_media_port`、`remote_media_ip`、`remote_media_port`；再依次从 `bridge_uuid`、`signal_bond`、`Other-Leg-Unique-ID` 尝试找到一条本机桥接腿。使用 `--no-peer` 可只抓指定腿。主通话无有效端点则报错；另一条腿已结束或不可用则在清单中警告，保留主腿。不同 FS 主机须分别执行，报告可同时输入多个抓包目录。

普通 IPv4 包按双向 IP、UDP 端口限制，核心表达式例如：

```text
udp and (((src host 10.0.0.1 and src port 16000 and dst host 10.0.0.2 and dst port 24000)
       or (src host 10.0.0.2 and src port 24000 and dst host 10.0.0.1 and dst port 16000)))
```

2.0 还保留同一地址对间的 IPv4 后续 TCP/UDP 分片，以及 IPv6 TCP/UDP 扩展头链。这些包在 BPF 层无法严格按端口筛选，解码后进一步选择会话；不会称为严格四元组采集。

NAT、bypass/proxy media、非标准 channel variables 可能使查询结果不适用。应先核对计划中的端点；仅有通话 UUID 不能取回事后已经消失的媒体。这里的过滤器是抓包开始前的快照，不自动跟随 re-INVITE、NAT 重绑定或新的桥接关系。

也可以完全绕过 FS 查询，重复指定明确端点；同样支持 IPv6：

```bash
voice-tools capture start --host fs-prod \
  --flow 10.0.0.1 16000 10.0.0.2 24000 \
  --flow 10.0.0.1 16001 10.0.0.2 24001 \
  --seconds 45 --out outputs/manual-capture
```

第二组只是你已确认的独立 RTCP 端点示例，工具不会猜测 RTP+1。自动模式只抓查询到的音频 UDP 四元组；不自动包含 SIP、视频、独立 RTCP 端口或其他主机流量。手工模式的 `--dry-run` 完全不访问网络。

### 时长、权限和文件上限

- `--seconds`：1–3600 秒，默认 60。dumpcap 自身限时，外层 GNU timeout 兜底；SSH 断开后仍受远端时限约束。
- `--snaplen`：64–65535，默认 65535。抓包记录仍可能被截断；发现截断会标 partial。
- `--max-mib`：1–512，默认 64。默认 `--backend dumpcap` 用实际文件字节限额，预留一个最大记录的边界，不以 snaplen 换算包数。达到额度仍会提前退出并标 partial，单通模式不环形覆盖。`--backend tcpdump` 保留旧版最坏包长推导 `-c` 的保守算法。
- `--sudo`：执行 `sudo -n timeout ... dumpcap ...`，要求已配置非交互权限。工具不配置 sudoers；输出文件预建并保留 SSH 用户的可读权限。
- 远端目录 `/tmp/voice-tools-<随机字符>` 为 0700，PCAP 预建为 0600；本地输出目录也是 0700。完整 uuid_dump 不写入产物，只保存媒体端点与腿 UUID。

### 取回与恢复

成功后目录包含：

| 文件 | 内容 |
|---|---|
| `capture.json` | 端点、BPF、主机、UTC 时间、参数、远端路径、状态、SHA-256，以及抓包计数、截断包、dumpcap 健康状态 |
| `session.pcap` | 校验通过的抓包文件 |
| `tcpdump.log` | 保留兼容文件名，保存所选后端的标准错误和计数 |

远端 SHA-256 在 SCP 前后各读取一次，并与本地比较；确认一致后才把 `.part` 改名为 `.pcap`。这验证传输内容一致性，完整协议解析在报告步骤完成。远端文件默认保留，便于检查和重试，使用后按运维保留策略自行清理。

```bash
# 复制 capture.json 中的 remote_dir；请等待原抓包时限结束
voice-tools capture fetch --host fs-prod \
  --remote-dir /tmp/voice-tools-abcdefghijkl --out outputs/recovered
```

`fetch` 仅重试取回，不再查询 FS，也不能证明原抓包进程正常退出；恢复报告时使用 `--pcap ... --rtp-port ...`，或保留原清单供人工关联。Ctrl-C 中断本地命令时，远端可能继续至原定时限，清单保留恢复路径。

## 2. 多录音、多 PCAP 报告

录音支持单/双声道、8–48 kHz、单份不超过 3600 秒，解码 float32 上限 512 MiB。PCM16 WAV 直接读取；MP3、FLAC、M4A、OGG 及其他受 FFmpeg 支持的封装音频使用本地 FFmpeg/ffprobe。裸 PCM/ALAW/MULAW 须先通过 `voice-tools audio prepare` 显式指定格式、采样率和声道。保持真实声道，不把单声道复制为双轨。

PCAP/PCAPNG 分析需要本机安装 Wireshark 的 `tshark`，可用 `--tshark /path/to/tshark` 指定。单份文件或连续组不超过 2 GiB，默认分析前 250000 包，`--max-packets` 最多 1000000；超过上限会标记部分结果并退出 3。

```bash
# 直接使用 capture 产物：校验 SHA-256，读取该 PCAP 对应的媒体端口
voice-tools report build \
  --capture outputs/call-capture \
  --audio data/fs-recording.wav data/terminal-recording.mp3 \
  --include-audio --out outputs/call-report

# 已有 PCAP；可只给录音，也可只给 PCAP
voice-tools report build \
  --pcap data/fs-a.pcap data/fs-b.pcapng \
  --audio data/a.wav data/b.wav \
  --rtp-port 16000 --rtp-port 24000 \
  --clock-rate 111=48000 \
  --include-audio --out outputs/multi-report

open outputs/multi-report/report.html
```

没有 SIP/SDP 的 RTP 抓包经常需要 `--rtp-port` 进行 Decode As。只指定已确认的 RTP 端口；误把普通 UDP/RTCP 强制解码可能产生错误识别。`--capture` 的端口规则只作用于相应 PCAP；手工 `--rtp-port` 作用于全部输入 PCAP。

PT 0/8 的 RTP 时钟按 8000 Hz，PT 9（G.722）也按 RTP 规范使用 **8000 Hz**。动态 PT 优先按导出清单中的 SDP 端点、时间段映射；没有映射时未知，可重复 `--clock-rate PT=HZ` 指定；同一流有未知或变化的时钟时，不显示整体抖动值。映射需适合本批输入，不同抓包使用同一 PT 但不同编码时应分批分析。

报告包含：

- **录音**：每声道波形、峰值、活动电平、低于门限比例与片段、削波样本比例、双声道相似提示。默认活动门限 −45 dBFS，可用 `--threshold-db` 调整；门限不是语义或语音识别。`--include-audio` 复制/解码为 PCM16 WAV 供浏览器离线试听，不归一化、不降噪。
- **PCAP**：按方向、端点、SSRC 分流；序号回绕、缺口候选、重复候选、乱序、序号大跳变、最大到达间隔、已知时钟下 RFC3550 平滑抖动、每秒包数图、截断包及分析上限。
- **来源**：每份输入 SHA-256、路径、参数、抓包元数据和处理错误，保存在 `report.json`。一份输入失败时，其他输入继续处理；HTML 保留失败项。

同一 SSRC 切换 payload type 不会拆成不连续序号流。相邻扩展序号变化超过 3000 时分段，并标记跳变；该处缺失量未知，不能把大量跳变直接计为丢包。乱序回补会减少最终缺口计数。抓包开始前/结束后的缺失包无法估计。

2.0 用 `--decode-rtp` 显式启用明文单声道 G.711（PCMU/PCMA）重建，输出 payload 拼接和 timestamp 补零两份 WAV；同时解析 RTCP SR/RR/XR。详见 [2.0 分析边界](capture-v2.md#媒体分析边界)。报告不会自动进行录音与 PCAP 时间轴对齐、跨抓包点去重、SRTP 解密、MOS 预测或根因判定。每份 PCAP 的缺口可能来自网络、抓包点丢弃、过滤和截断；`any` 接口上的重复也可能来自重复观测。单点抓包无法测单向网络延迟。音频低能量不能单独证明 AI/TTS 故障。

所有页面资源位于报告目录内，可离线打开，不请求 CDN。分享时须连同 `audio/`、`rtp-audio/` 和 `report.json` 一起复制；报告包含 IP、文件路径，启用试听时还包含录音。

## 3. 退出码与自动化

| 退出码 | 含义 |
|---|---|
| 0 | 计划已生成、正常抓包已取回、恢复取回成功，或报告处理完成；不代表音质正常 |
| 2 | 参数、端点、依赖或运行失败；若已创建目录，检查 `capture.json` 的恢复路径 |
| 3 | 抓包无数据、达到包数上限或异常提前退出但已取回，或报告存在文件错误/分析包数上限 |

`voice-tools --json capture ...`、`voice-tools --json report ...` 输出统一 JSON 封套；`summary` 和 `artifacts` 指向具体结果。`voice-tools schema --tool capture` / `--tool report` 可离线读取实际参数。

## 4. 本地验证范围

测试使用合成录音/PCAP 和模拟 SSH/SCP，验证 BPF、桥接查询、传输失败恢复、摘要校验、序号算法、HTML 转义、多文件隔离与机器入口；有 tshark 时执行真实离线解码，有 tcpdump 时执行离线 BPF 编译。没有配置或连接任何生产主机。实际 FS 版本、ESL 权限、NAT 端点、tcpdump/sudo 和 SCP 仍需用活动通话在目标主机验证。

1.0 历史验收：2026-09-19 基于当时 `main` 的独立 PR 工作树执行 156 项测试，全部通过；其中抓包、会话检索、HOMER 联动及报告新增 41 项。使用真实 tshark、FFmpeg 和 tcpdump 做离线验证。桌面/手机渲染和实际试听尚未验收；本地浏览器此前因 URL 策略拒绝 `file://`，资源检查不等于播放成功。
