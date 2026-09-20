# sngrep 安装与使用

sngrep 是终端里的 SIP 呼叫时序查看工具，支持实时采集和读取已有 PCAP。和本项目配合时，推荐先由 voice_tools 采集、筛选并取回文件，再用 sngrep 离线查看。完整流程见[联动快速说明](sngrep-workflow.md)。

## 安装位置与依赖

| 使用方式 | 安装在哪里 | 所需组件 |
|---|---|---|
| 推荐：查看已下载的通话 | 本机 | sngrep；PCAPNG 转换/分片合并另需 editcap、mergecap |
| 在服务器实时看 SIP | 目标服务器 | sngrep，以及该服务器允许的抓包权限 |
| voice_tools 原有采集和报告 | 维持现有部署 | 依赖见[号码抓包](capture-by-number.md)及[报告指南](capture-report.md)；安装 sngrep 不会替代这些依赖 |

### macOS

已安装 Homebrew 时执行：

```bash
brew install sngrep
sngrep -V
sngrep -h
```

若需要转换 PCAPNG 或合并文件，先检查是否已有 Wireshark 命令行工具：

```bash
command -v editcap
command -v mergecap
command -v tshark
```

已安装 Wireshark App 但命令不在 PATH 时，可使用 `/Applications/Wireshark.app/Contents/MacOS/editcap` 等完整路径。若尚未安装这些工具，可使用 Homebrew 的命令行工具包：

```bash
brew install wireshark
```

### Debian / Ubuntu

优先使用当前发行版仓库：

```bash
sudo apt-get update
sudo apt-get install sngrep
sngrep -V
```

需要本机分析/转换工具时，可另装 `tshark` 和 `wireshark-common`。安装过程可能询问非 root 抓包权限；按已有运维策略选择，离线读取文件不需要为此修改用户组或设备权限。

```bash
sudo apt-get install tshark wireshark-common
```

### Fedora / RHEL 系列

先确认已启用的软件仓库是否提供该包：

```bash
dnf info sngrep
# 上一步能找到包时再安装
sudo dnf install sngrep
sngrep -V
```

若提示找不到软件包，需要根据发行版及组织的软件源策略配置仓库。官方[二进制安装说明](https://github.com/irontec/sngrep/wiki/Installing-Binaries)提供 Irontec 仓库/COPR 路径；不同发行版可用性不同，不要直接照抄其他系统版本的软件源。本文不自动启用第三方仓库。

## 读取与合并已有抓包

```bash
# 读取经典 PCAP，保留关联媒体供查看/保存
sngrep -F -r -I session.pcap

# 将 PCAPNG 转成用于查看的副本；输入与输出使用不同文件名
editcap -F pcap session.pcapng session-view.pcap
sngrep -F -r -I session-view.pcap

# 仅合并同一采集点、连续时间段的分片
mergecap -F pcap -w window-view.pcap part-000001.pcap part-000002.pcap
sngrep -F -r -I window-view.pcap
```

保留原始文件、清单和摘要。经典 PCAP 不能保留 PCAPNG 的全部元数据；不同接口/封装类型也不一定能合并成一个经典 PCAP。转换失败时检查采集来源或分别查看，不强行丢弃接口信息。

### 常用交互

以下是本次核对的默认按键；不同窗口的 F2/F3 功能可能不同，以底部提示和 F1 帮助为准。macOS 可能需要按 Fn 才能发送功能键。

| 所在窗口 | 操作 | 默认按键 |
|---|---|---|
| 通话列表 | 上下选择 / 打开时序 | 方向键 / Enter |
| 通话列表 | 选择多条 dialog 后一起查看 | Space 选择，Enter 打开 |
| 通话列表 | 查找、缩小显示范围 | `/` 或 F3 |
| 通话列表 | 保存所选/过滤结果 | F2，进入保存窗口后确认范围与格式 |
| 通话时序 | 查看 SIP 原文 | F6 |
| 任意支持的窗口 | 查看帮助 / 返回 | F1 / Esc 或 `q` |

在保存窗口需要媒体时，选择 `.pcap (SIP + RTP)`，并核对是保存所选 dialog 还是全部结果。该选项依赖 `-r` 及已识别的媒体；另存文件不能补回原本缺失的包。

查看顺序建议：先确认 Call-ID 和号码，再看响应码与建立/挂断顺序，最后核对 SDP 的媒体地址、端口及变化。多条 Call-ID 不会因号码相同就自动成为同一个业务会话。

## 现场实时查看：可选用法

以下示例适用于有抓包权限的 Linux 主机，需要在呼叫开始前运行。将 `192.0.2.10`、端口和接口改为实际范围；`any` 是 Linux 常用接口名，macOS 可用 `tcpdump -D` 查看后选择 `en0` 等实际接口。

```bash
# 仅看该主机 5060 端口的 SIP；按 Ctrl+C 或退出界面结束
sudo sngrep -F -d any '.*' 'host 192.0.2.10 and port 5060'

# 同时观察并保存 SIP 与关联媒体
# 示例媒体端口范围 16000–24000，必须覆盖实际 RTP/RTCP 范围
sudo sngrep -F -r -d any -O /tmp/sngrep-call-001.pcap \
  '.*' 'host 192.0.2.10 and (port 5060 or udp portrange 16000-24000)'
```

`'.*'` 是匹配所有 SIP 消息的正则，后一个参数才是 BPF。显式分开写，避免把整段 BPF 当成消息正则。仅需 SIP 时限制 5060 没问题；要保存媒体时，不能只允许 SIP 端口。非标准 SIP 端口也需加入范围。

`-O` 的输出路径使用新的文件名，避免覆盖旧包；实时抓包文件包含实际信令和媒体，按现有取证权限与留存规则管理。

### 无界面短时采集

Linux 已安装 GNU `timeout` 时，可限制运行时间：

```bash
sudo timeout --signal=INT --kill-after=5s 60s \
  sngrep -F -N -q -r -d any -O /tmp/sngrep-call-002.pcap \
  '.*' 'host 192.0.2.10 and (port 5060 or udp portrange 16000-24000)'
```

`-N` 不显示界面，`-q` 隐藏会话计数。GNU timeout 到期通常返回 124，应检查输出文件和实际覆盖范围；退出码 0 也不能证明抓包完整。这个命令只有时间限制，没有现有 dumpcap 任务的磁盘预算、冻结与恢复机制，不用于替代长期环形采集。

## 筛选与参数速查

```bash
# 号码的宽松全文匹配：仅用于人工找线索，不表示精确主叫筛选
sngrep -F -r -I session.pcap '1001'

# 无界面离线筛选并另存；输出文件不应替代原始证据
sngrep -F -N -q -r -I session.pcap -O selected-1001.pcap '1001'
```

`1001` 可能命中主叫、被叫、`91001`，甚至其他 SIP 头字段。精确主叫/被叫筛选应使用 `voice-tools capture by-number --caller/--callee`。正则是载荷匹配，不是 SIP 字段解析。

| 参数 | 用途 |
|---|---|
| `-V` / `-h` | 查看已安装版本 / 参数帮助 |
| `-F` | 忽略默认配置文件，便于复现命令行为 |
| `-I 文件` | 离线读取 |
| `-O 文件` | 保存解析后接收的 SIP/关联媒体 |
| `-r` | 保存关联 RTP 载荷；本次样本也包含关联 RTCP |
| `-d 接口` | 选择实时抓包接口 |
| `-N -q` | 无界面、隐藏计数 |
| `-l 数量` | 限制会话记录数量；不是包数或文件大小 |
| `-R` | 达到会话上限时移除旧会话；不是磁盘文件轮转 |

## 常见问题与边界

- **列表为空，但 Wireshark 能看到 RTP：** 文件可能只有媒体、没有可识别的 SIP，或中途抓取的不完整会话被默认设置过滤。用原始 PCAP 做媒体分析；不要把空列表当作无媒体的证明。
- **只有信令，没有媒体：** 检查是否加 `-r`、BPF 是否包含媒体、抓包点能否同时看到 SIP 和媒体。sngrep 依赖关联状态，未关联媒体可能不保存。
- **采集一段时间后数据停止增加：** 检查 `-l` 会话上限。不开 `-R` 时可跳过后续包；开启后也可能移除仍在通话的旧状态，遗漏其后续媒体。
- **需要文件轮转：** sngrep 支持外部重命名后发 SIGUSR1 重开输出，但还要自行管理容量、保留和关闭状态。长期任务继续使用本项目的 [dumpcap 环形采集](capture-v2.md)。
- **TLS/SRTP：** 能捕获包不代表能解密内容；官方只声明部分 TLS 支持，不保证任意现代 TLS/SRTP 会话都能展示或播放。

## 核对依据

2026-09-19 核对官方文档、固定源码及本机 sngrep 1.8.4。前序 14 组离线合成实验验证了关联 SIP/RTP/RTCP 保存、纯媒体遗漏、号码误匹配、BPF 及会话轮换边界。本文的离线命令另做执行核对；Linux 安装、实时采集及生产环境未验收，包版本/可用性以目标机器为准。

- [官方项目与 README](https://github.com/irontec/sngrep)
- [官方二进制安装说明](https://github.com/irontec/sngrep/wiki/Installing-Binaries)
- [Homebrew sngrep](https://formulae.brew.sh/formula/sngrep)
- [固定提交命令手册](https://github.com/irontec/sngrep/blob/1642c54a37dd9e0e91f523841d5e297e4b0b114a/doc/sngrep.8)
- [1.8.4 默认按键](https://github.com/irontec/sngrep/blob/v1.8.4/src/keybinding.c)
