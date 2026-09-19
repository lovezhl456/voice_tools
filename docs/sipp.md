# SIPp 安装、批量测试与工作台联动

适用于 voice_tools 0.9.1 开发分支。资料核对日期：2026-09-19。本机验证基于 SIPp 3.7.7；这不是对“最新版本”的声明。

## 1. 选哪个后端

| 目标 | 工作台 / 命令 | 执行与证据 |
| --- | --- | --- |
| 一组真实功能用例，包含播放录音、按键、接收录音 | 批量 → 功能批量；`sip batch` | 每通独立 PJSUA2 进程、录音、事件和结果，最多并发 32 通 |
| 信令 / 按键性能压力，控制 CPS、在途并发与总量 | 批量 → 性能压力；`sip sipp-load` | 一个独立 SIPp 进程生成负载；保存统计、退出码和错误日志 |
| 精确选择原始抓包的一条发送流回放 | `sip sipp-prepare` → `sip sipp-run` | 原有单通话 PCAP 回放；不自动变成压力活动 |

功能批量与压力测试分开运行。PJSUA2 保留原有功能步骤，不把它的并发线程数当作 SIPp 压力能力。SIPp 包不接收 voice_tools 业务断言，也不生成 RX WAV；成功呼叫数只能说明 SIPp 场景走完。

工作台保持本地静态页面：负责准备、导出与导入结果；真实执行在终端进行。浏览器不自动拨号，不展示虚构的实时进度。

## 2. 安装 SIPp

先确认 `voice-tools --version` 为包含本功能的 `0.9.1` 或后续版本。开发分支使用独立 checkout，按项目 README 的虚拟环境安装步骤执行 `python -m pip install -e .`；不要覆盖另一项开发任务正在使用的环境。

### macOS / Homebrew

```bash
brew install sipp
sipp -v
sipp -h
```

安装完成后，检查版本输出是否包含 `PCAP`。仅等待 / SIP INFO 场景不需要 PCAP 发包；RFC4733 DTMF 和原始 RTP 回放需要。Homebrew 配方与构建选项会变化，以本机输出为准。

macOS 对原始套接字和 PCAP 发包可能有限制：能显示 `PCAP` 不代表当前账号可以发包。先跑一通小场景；出现权限错误时查 `sipp.log`。本项目不自动提权、修改系统网络或网关配置。需要稳定的媒体压力环境时，优先在专用 Linux 测试机验证。

### Ubuntu / Debian

```bash
sudo apt update
sudo apt install sip-tester
sipp -v
sipp -h
```

Debian / Ubuntu 的包名为 `sip-tester`，安装后的命令为 `sipp`。版本取决于发行版及启用的软件源；若仓库没有 `sip-tester`，使用下面的源码路径。按实际运行账号验证绑定端口和 PCAP 发包权限。

### 从官方源码编译

先安装依赖。Ubuntu / Debian 示例：

```bash
sudo apt install build-essential cmake git libssl-dev libncurses-dev libpcap-dev
```

macOS 示例：

```bash
xcode-select --install
brew install cmake openssl@3 ncurses libpcap
```

使用官方 Git 标签。下面固定 3.7.7，便于复现；升级时重新核验选项和场景。

```bash
git clone --branch v3.7.7 --depth 1 https://github.com/SIPp/sipp.git
cd sipp
cmake -S . -B build -DUSE_PCAP=1 -DUSE_SSL=1
cmake --build build -j 4
./build/sipp -v
```

如果 macOS 的 CMake 找不到 OpenSSL，可在配置命令中增加：

```bash
cmake -S . -B build -DUSE_PCAP=1 -DUSE_SSL=1 \
  -DOPENSSL_ROOT_DIR="$(brew --prefix openssl@3)"
```

使用官方 release 附件源码包或完整 Git 标签。部分 GitHub 自动归档不能生成正确的版本字符串，不要把占位版本头当作经过验证的发行版。无需安装到系统目录：voice_tools 支持 `--sipp /absolute/path/to/build/sipp`。

SIPp 是独立原生程序，`pip install voice-tools` 不会安装它。功能批量另需 PJSUA2，参见 [SIP 主文档](sip.md)。

## 3. 先做本机信令冒烟

两个终端分别启动接收端和发送端，仅访问回环地址：

```bash
# 终端 A：接收 1 通
sipp -sn uas -i 127.0.0.1 -p 5090 -m 1 -timeout 30s -nostdin
```

```bash
# 终端 B：发送 1 通
sipp 127.0.0.1:5090 -sn uac -i 127.0.0.1 -p 5092 \
  -r 1 -l 1 -m 1 -timeout 20s -timeout_error -nostdin
```

这验证基本 SIP 信令，**不证明语音、按键、注册、生产互通或容量已通过**。测试自有网关时，把目标 IP、主被叫账号和本机可达 IP 改为真实参数；容器 / NAT 的 SDP 地址也必须能被对端访问。

## 4. 工作台：功能批量

打开 [SIP Case Studio](sip-studio/index.html)，进入「批量」。

1. 从用例库选择用例并加入队列，可多次加入；调整顺序和每项重复次数。
2. 设置并发上限，默认 2，范围 1–32；展开后最多 10000 通。
3. 设置端口池：SIP 起始端口 0 表示自动分配；显式端口按槽位递增。RTP 起始端口必须为偶数，每槽预留 4 个端口，含 PJSUA2 备用端口对。不要同时运行重叠的端口池。
4. 下载 `queue.json`，把 WAV / media.json 放到其中的相对路径。加入队列时保存参数快照；之后修改原用例不会改动已有队列项，应重新加入。
5. 校验后执行，两个命令使用不同的新目录：

```bash
voice-tools --json sip batch queue.json --dry-run --out outputs/batch-plan-001
voice-tools --json sip batch queue.json --out outputs/batch-run-001
```

所有用例先通过离线预检再开始呼叫。任务按队列顺序启动，完成顺序取决于耗时；一个失败不会阻断后面的任务。端口只有在上一子进程结束并清理后才回收。并发使用同一注册账号时，注册器可能覆盖 Contact；需要多账号时配置成不同用例，按服务器的多注册策略验证。收到 Ctrl-C / SIGTERM 后不再调度：运行项标记中断、未启动项取消、结果落盘。

```text
batch-run-001/
  batch-result.json       # 原子更新的队列状态与逐项结果
  summary.csv             # 最终摘要
  job-00001/
    scenario.json         # 本次端口分配与绝对素材路径
    cli.log
    run/
      result.json
      events.jsonl
      rx.wav
      tx_source.wav
```

接收失败时不会保证所有音频文件均存在。`tx_source.wav` 是本地播放源时间线，不是远端接收证据。结果 JSON 可以导回工作台查看；导入的是快照，重新导入才刷新。

## 5. 工作台：生成 SIPp 场景包

在「批量 → 性能压力」中，从队列选 **一个** 用例。用左侧队列保存多个候选场景；每次压力包只对应选中的一个，不把不同呼叫流程悄悄混合。

- **总呼叫数 `-m`**：本次最多启动多少通，不使用队列里的重复次数。
- **速率 `-r` / `-rp 1000`**：每秒计划启动多少通。
- **并发上限 `-l`**：同时在途呼叫数，达到后暂缓新呼叫；实际 CPS 可能低于设置值。
- **总时限 `-timeout` + `-timeout_error`**：到时退出并视为不完整 / 失败，避免未完成总量却报告成功。
- **本机 IPv4 / SIP / RTP 端口**：用于 SIP 与 SDP；不能留成无法被对端访问的地址。
- **目标列表**：每行一个完整 SIP URI，留空使用用例目标；同一包必须是相同 IPv4 网关和端口，不同被叫号码使用 CSV 顺序轮换。

支持矩阵：

| 现有用例字段 / 步骤 | SIPp 压力导出 |
| --- | --- |
| wait / hangup | 保留顺序；结束时正常 BYE |
| DTMF SIP INFO | 每键 INFO + 200 响应，再等待持续时间与间隔 |
| DTMF RFC4733 | 生成发送方向 PCAP，保留按键顺序 / 重复键 / 持续时间 / 间隔；telephone-event PT 固定 101，网关必须接受 |
| PCMA / PCMU | 生成对应 SDP；等待 / 按键场景不发送连续语音 RTP |
| WAV play / play_media | 阻止压力导出，保留在功能批量；原包回放使用原有 sipp-prepare |
| 注册 / 认证 / 代理 / early media 录音 | 阻止压力导出，使用 PJSUA2 功能路径 |
| 业务断言 / 语义判断 | 不支持，不把场景成功解释成业务通过 |

SIP INFO 的响应等待会增加步骤时长。RFC4733 包使用明确生成的 PCAP，不依赖 SIPp `play_dtmf` 的隐含 payload type、预热包和间隔。SIPp PCAP 发送不保证线速或毫秒级调度精度；容量结论还需监测发压机 CPU、网络和调度延迟。

下载的 `sipp-load.zip` 包含：

```text
load.json       # 可审阅、可重新生成的压力参数与场景快照
scenario.xml    # SIPp 可直接读取的 XML 场景
targets.csv     # SEQUENTIAL + 完整被叫 URI
run.sh          # 固定参数的启动脚本
README.txt
dtmf-N.pcap     # 仅当选中场景包含 RFC4733 时存在
```

```bash
unzip sipp-load.zip -d sipp-package
voice-tools --json sip sipp-load sipp-package --dry-run --out outputs/load-plan-001
voice-tools --json sip sipp-load sipp-package --out outputs/load-run-001
# 自行编译的 SIPp：
voice-tools sip sipp-load sipp-package --sipp /absolute/path/to/sipp \
  --out outputs/load-run-002
```

`--dry-run` 不查找 SIPp 二进制、不发 SIP，但核对 XML、CSV 和 PCAP 与 load.json 是否一致。修改参数后重新生成，不能只改 XML / CSV 再交给受控后端运行：

```bash
voice-tools sip sipp-export sipp-package/load.json --out sipp-package-v2
```

也可以完全独立于 Python 执行 `sh sipp-package/run.sh`。直接脚本执行把 `statistics.csv`、`errors.log` 写在包目录，不生成 voice_tools 的 result.json；重复执行前复制到新目录，以免混淆日志。脚本从自身目录解析素材，不依赖调用时的工作目录。

## 6. 阅读结果与逐步加压

通过 `sipp-load` 执行时，输出目录保存输入快照、`sipp.log`、`statistics.csv`、可用的 `errors.log` 和 `result.json`。无错误时错误日志可能不存在。

- 原生退出码 **0**：所有处理的呼叫成功；还要核对统计中的总量。
- **1**：至少一通失败。
- **97**：内部命令要求退出；**99**：正常退出但未处理呼叫；均不当作测试成功。
- **253**：RTP 验证失败；**-1 / 255**：致命错误；**-2 / 254**：套接字绑定致命错误（不同运行环境展示有符号或无符号值）。启用 `-timeout_error` 后全局超时退出为失败；以 result.json 与日志核对具体原因。
- voice_tools：校验 / 环境错误退出 **2**；执行失败或中断退出 **3**；计划或完成退出 **0**。使用 `result.json.status` 同时检查是否中断，不只看日志末行。

统计常见字段包括 `SuccessfulCall(C)`、`FailedCall(C)`、`CurrentCall` 和响应时间。响应时间是在 INVITE 发出到 200 收到之间测得，不是整段业务耗时或首包音频延迟。统计字段以本机版本表头为准。

建议先 `calls=1, concurrency=1, rate=1`，再小规模递增。总时限应大于排队和单通最长持续时间；并发受限时不能只用 `calls / rate` 估算。记录发压机与被测机 CPU / 内存 / 网络、失败码和各档统计，才能判断瓶颈；本次本机验证不等同生产容量测试。

## 7. 常见问题

| 现象 | 检查 / 处理 |
| --- | --- |
| 未找到 sipp | `sipp -v`；检查 PATH 或指定 `--sipp` |
| bind / Address already in use | 换本机 SIP / RTP 端口；检查重叠队列，不自动终止别的进程 |
| 401 / 407 | 当前压力模板未实现 Digest；改用 PJSUA2 功能批量或另行设计认证场景 |
| 488 / 没收到按键 | 核对 G.711 codec、telephone-event PT 101、SDP 可达性和 PCAP 发包权限 |
| PCAP permission denied | SIPp 编译支持不等于账号有原始发包权限；核对运行环境，或用纯信令 / SIP INFO 场景排查 |
| 超时而未完成总量 | 延长总时限，检查单通持续时间、并发上限、响应失败；不要把结果改成成功 |
| 导出被 WAV 或 media.json 阻止 | 功能批量支持这些步骤；压力模板当前不转换连续语音素材 |
| SIPp 完成但业务不正确 | SIPp 场景成功不是业务断言；查看功能拨测录音和业务证据 |

## 官方资料

2026-09-19 通过官方文档索引核对场景、媒体、安装与退出行为；本机 SIPp 3.7.7 的 `-h` / `-v` 用于实际选项复核。

- [SIPp 官方仓库 / 构建说明](https://github.com/SIPp/sipp)
- [安装](https://sipp.readthedocs.io/en/latest/installation.html)
- [编写场景](https://sipp.readthedocs.io/en/latest/scenarios/ownscenarios.html)
- [场景动作与媒体](https://sipp.readthedocs.io/en/latest/scenarios/actions.html)
- [运行与退出码](https://sipp.readthedocs.io/en/latest/sipp.html)
- [Debian sip-tester 包](https://packages.debian.org/stable/sip-tester)
- [Homebrew 配方](https://formulae.brew.sh/formula/sipp)

仓库提供可复现的低负载脚本（仅绑定 / 访问 127.0.0.1；需 PJSUA2 和 SIPp，测试端口 32000–32007、32100–32101、32200–32201 应空闲）：

```bash
PYTHONPATH=src python scripts/sip_batch_smoke.py --sipp /absolute/path/to/sipp --out outputs/batch-smoke-001
# 在具备 PCAP 原始发包权限的环境另加 --include-pcap
```

本版本的具体测试记录见 [批量与 SIPp 验收](sip-batch-validation.md)。
