# 多主机批量抓包、会话检索与 HOMER 联动

2.0 长期环形采集、ESL 和一键排障见 [capture-v2](capture-v2.md)。本页介绍限时批次与手动检索。

命令流程：**capture batch → sessions index/search → sessions correlate → sessions export → report build**。也可先用 HOMER 按号码和时间找到 Call-ID，再从已抓取的本地 PCAP 提取媒体。

批量模式从命令启动后开始抓包，不要求先指定 UUID；每台主机采集明确 IP/CIDR 和端口范围内的多通电话。没有事先抓到的历史 RTP，不能用 HOMER 的 SIP 重建 PCAP 补回。单通 UUID 抓包继续使用[原指南](capture-report.md)。

## 1. 配置主机与并发抓包

复制[主机清单示例](../examples/capture/hosts.example.json)，替换 SSH 别名、接口、实际 IP 和 FS 端口范围。最多 16 台主机同时执行，清单中 `name` 必须唯一；支持可选 `ssh_port`、`identity`、`fs_cli`。不把密码写进清单。SSH 认证、known_hosts 和 sudo 权限仍沿用已有运维配置。

```bash
# 完全离线检查：生成各机 BPF 和计划，不连接任何主机
voice-tools capture batch --inventory examples/capture/hosts.example.json \
  --seconds 300 --dry-run --out outputs/batch-plan

# 连接你已配置的主机，抓取 5 分钟；每 60 秒分片
voice-tools capture batch --inventory data/hosts.json \
  --seconds 300 --segment-seconds 60 --max-mib 512 \
  --fs-snapshot-seconds 10 --max-snapshot-channels 100 \
  --out outputs/batch-001
```

各主机并发启动，**不保证同一微秒开始**。清单保存本地开始/结束时间，PCAP 保留各捕获点的包时间；比较跨机器延迟前须确认时钟同步。工具不调整系统时钟。

BPF 对普通包限制配置的 IP/CIDR、SIP UDP/TCP 端口和 RTP/RTCP UDP 范围；为重组还保留该地址范围内 IPv4 后续分片及 IPv6 TCP/UDP，不能在此处严格限制其端口，不提供默认全网全端口抓包。SIP TLS 等加密流量无法由本地 SIP 解析器直接检索，FS 快照可补充映射。非标准 SIP 端口须在清单中列出。

### 大小、分片与失败隔离

| 参数 | 默认值与范围 |
|---|---|
| `--seconds` | 300 秒；默认 dumpcap 后端 1–604800 秒，旧 tcpdump 后端 1–86400 秒 |
| `--segment-seconds` | 60 秒；10–3600 秒 |
| `--max-mib` | **每主机全部分片** 512 MiB；1–16384 MiB |
| `--snaplen` | 65535；64–65535 |
| `--fs-snapshot-seconds` | 10 秒；5–30 秒，0 关闭 |
| `--max-snapshot-channels` | 每轮每机最多 100 条腿；1–500 |

2.0 默认使用远端 Python agent＋dumpcap，按实际文件字节限额和时间旋转，不用 `-c` 推导总包数。`capture batch` 是窗口模式，达到文件数/字节预算可能提前结束并标 partial；`capture ring-start` 才循环覆盖旧片。窗口模式以 `ceil(seconds/segment_seconds)+1` 个槽位平分额度，每槽预留一个最大记录；高流量提前触发大小旋转后，可能在耗尽总额度之前先用完文件数。单片或同采集点连续组最多 2 GiB，建议按目标会话冻结短窗口。`--backend tcpdump` 保留旧算法和恢复命令。

每个分片通过 SCP 取回，前后远端 SHA-256 与本地一致后才确认成功。一台机器连接、抓包或传输失败，不丢弃其他机器成功产物。远端目录保持私有，不自动删除。默认后端在 Ctrl-C 后发送停止请求，保留 job.json 以便 status/fetch；断联时仍由远端时限兜底。

```text
batch-001/
  batch.json                 # 各机计划、执行状态、主机清单路径
  job/job.json               # 多机任务与恢复位置
  capture/fs-a/
    host.json                # BPF、冻结窗口、分片摘要与完整性状态
    part-000001.pcap
    events.jsonl             # 周期快照及可选 ESL 事件
  capture/fs-b/...
```

默认远端依赖：SSH/SCP 服务、Python 3.9+、dumpcap、sha256sum、标准 shell；快照另需 fs_cli。旧 tcpdump 后端另需 GNU timeout 和 find。`sudo: true` 使用非交互 sudo，不改 sudoers。

### FreeSWITCH UUID/Call-ID 快照

默认启用，先读取 `show channels as json`，再对本轮选中的 UUID 批量读取 `uuid_dump`，保存：

- UUID、SIP Call-ID、桥接 peer UUID、主叫/被叫号码；
- 当前媒体双向四元组；
- 观测时间、主机名与取样间隔。

完整 channel dump 和认证字段不写入产物。每轮 UUID dump 有 8 秒预算，每条最多 2 秒。2.0 事件日志轮转保留约 16 MiB/机（另有单条记录边界），内存最多保留 20000 条腿的映射。超出每轮腿数时轮转取样；短于采样间隔的电话可能漏掉。实际轮次还受 FS/SSH 查询耗时影响，不能把采样点当作精确接通/挂机事件。超限、认证失败、断连和查询失败会记录缺口；清单与索引继续携带 partial。清单可配置 ESL 来补充短通话事件，见 [2.0 指南](capture-v2.md)。

跨 B2BUA 的两条腿可能有不同 Call-ID。同机、相邻时间的 FS bridge UUID 映射及明确的业务 correlation ID 可作为关联线索，回执保留依据；号码相同不会被自动合并。

默认后端用 `capture ring-status --job outputs/batch-001/job` 和 `ring-fetch` 恢复，时间窗口见清单。下面的旧命令仅适用于 `--backend tcpdump` 产物：

```bash
voice-tools capture fetch-batch --host fs-a \
  --remote-dir /tmp/voice-tools-abcdefghijkl --out outputs/fs-a-recovered
```

恢复目录可直接传给 `sessions index --batch`。请保留原 FS 快照；恢复取回无法重建过去未采集的快照，也不验证原 tcpdump 是否正常退出。

## 2. 离线建立会话索引和检索

```bash
voice-tools sessions index --batch outputs/batch-001 --out outputs/index-001

# 精确号码匹配；按首末观测区间与时间窗口重叠筛选
voice-tools sessions search --index outputs/index-001 --number 1001 \
  --from '2026-09-19T14:00:00+08:00' --to '2026-09-19T14:05:00+08:00'

voice-tools sessions search --index outputs/index-001 --host fs-a --limit 100 --offset 0
voice-tools sessions search --index outputs/index-001 \
  --uuid 11111111-2222-3333-4444-555555555555
voice-tools sessions show --index outputs/index-001 --call-id 'call-a@example.net'
```

输出为可读 JSON；加顶层 `--json` 可获得统一封套。SQLite 索引保存在 `sessions.sqlite`，输入处理结果保存在 `index.json`。检索返回 Call-ID、主叫/被叫、UUID、观测主机、首末观测时间和观测条数，支持分页。Call-ID 是精确分组键；这里的会话数不等于 SIP 报文数，也不能直接称为业务通话总量。同 Call-ID 在两台机器观察到时聚合展示，保留每条证据来源，不在统计 RTP 时合并两个抓包点。

时间检索按观测区间重叠，并非由业务事件证明的真实通话起止。开始/结束时间必须包含时区。仅有一个观测点的电话只能按该点检索；没有 SIP 明文、没有快照、没有 HOMER 数据时，不能凭 RTP 自动推导电话号码或 Call-ID。

支持既有 PCAP/PCAPNG、单机恢复目录、独立快照和 HOMER JSON，一次可重复传入：

```bash
voice-tools sessions index \
  --pcap data/fs-a.pcap --pcap data/fs-b.pcapng --sip-port 5080 \
  --snapshots data/fs-sessions.jsonl \
  --homer-json outputs/homer-discovery/homer-search.json \
  --out outputs/combined-index
```

PCAP 索引需要本机 tshark，同采集点分片还需 mergecap。批次按主机自动连续重组，裸 PCAP 仅在 `--pcap-group SENSOR FILE FILE...` 中明确归组时重组；默认读取每个连续组最多 100 万包，`--max-packets` 上限 500 万；超限明确保留部分状态。一份输入损坏时回滚该输入的索引行，其余继续。重新索引使用新目录，不原地修改已建索引。检索、HOMER 关联和会话导出会继续携带索引的不完整状态。

## 3. 复用已有 HOMER CLI 查询

认证、URL、CA 和 token 全部使用当前 `voice-tools homer` / `homerctl` 的已有配置与 `HOMER_*` 环境变量。新工具通过参数数组调用当前 Python 环境的 HOMER CLI，不复制认证代码、不创建另一套配置、不在参数中传 token。初次接入仍按 [HOMER 使用约定](homer/ai-usage.md) 确认字段与 profile。

### 从本地会话查 HOMER

```bash
voice-tools sessions correlate --index outputs/index-001 \
  --call-id 'call-a@example.net' --padding 30 \
  --profile 1_call --max-requests 64 --out outputs/call-a-homer
```

这会执行：

1. 从本地观测首末时间向两端扩展 30 秒；
2. 调用原 CLI 的 `homer search --call-id ... --all`；
3. 调用 `homer trace --include-raw`，同时带入由本地 FS bridge 快照确认的关联腿 Call-ID（单次最多 20 个，超限明确标记）；
4. 保存 `homer-search.json`、`homer-trace.json` 和 `correlation.json`。

支持重复 `--node` 选择 HOMER 数据库节点。HOMER 节点名与 SSH 主机别名是不同概念，不能自动视为同一标识。

**HOMER 退出码 6 仍视为可用的部分结果**，原退出码、`completeness`、`unresolved_windows` 都保留；新命令退出 3。搜索失败或部分返回后，仍尝试独立 trace。原 `_ref` 的 `dbnode/profile/id/timestamp_us/call_id` 可随 JSON 导入索引；不会把单个数据库 row ID 当作跨节点唯一身份。

HOMER trace 自身可能包含服务端关联的其他 Call-ID 和窗口外报文，回执明确列出这些 Call-ID，不凭它们断言业务上的同一通电话。查询完成也不证明抓包完整。单个 CLI 调用上限 300 秒，单份导入/解析 HOMER JSON 上限 128 MiB；过大时保留返回文件并要求缩短窗口。

### 先从 HOMER 按号码发现会话

```bash
voice-tools sessions homer-search \
  --from '2026-09-19T14:00:00+08:00' --to '2026-09-19T14:05:00+08:00' \
  --caller 1001 --out outputs/homer-discovery

voice-tools sessions index --batch outputs/batch-001 \
  --homer-json outputs/homer-discovery/homer-search.json --out outputs/enriched-index
```

也可以直接使用原来的 `voice-tools homer search / trace / export`，把保存的 search/trace JSON 交给索引。HOMER SIP 重建 PCAP 不包含原始 RTP，也不能验证原始 IP 分片；本工具不会把它当作远端 tcpdump 的替代品。

## 4. 从大包中分离会话，再生成联合报告

```bash
# 默认只导出精确 Call-ID 的 SIP；此处显式纳入候选媒体
voice-tools sessions export --index outputs/index-001 \
  --call-id 'call-a@example.net' --include-media --out outputs/call-a-packets

voice-tools report build --session-export outputs/call-a-packets \
  --correlation outputs/call-a-homer \
  --audio data/fs-a.wav data/terminal.wav --include-audio \
  --out outputs/call-a-report
```

导出按独立采集点源（或已合并的同采集点分片组）生成 PCAPNG，并保留重组所需的 IP/TCP 依赖帧，保存原始包时间、源 SHA-256、筛选表达式和候选依据；不会把多主机重复观测拼成一个虚假的网络丢包率。源 PCAP 内容改变时拒绝使用旧索引导出。

媒体候选来自 SDP 声明、FS 周期快照和 ESL 事件。2.0 记录观察到的媒体阶段、codec、显式 RTCP 端点和 rtcp-mux；re-INVITE 更新同方向媒体阶段，终止事件关闭匹配阶段。这不是完整的 SIP offer/answer 状态机，拒绝的 offer 仍可能产生候选。相同四元组的相邻采样窗口会合并，支持长通话。**NAT、端口复用、端点变化、加密 SIP、未观测到 BYE 和时钟偏差可能带入或遗漏媒体**；这些不是确定归属证据。未看到 BYE/CANCEL 时，SDP 匹配窗口会延至源文件末尾，报告会提示风险。

联合报告读取导出清单自动配置各片的 RTP 端口，并显示 HOMER 查询状态、其他关联 Call-ID 和证据限制。HOMER 与导出清单的主 Call-ID 不一致时拒绝拼接。录音仍按自身时间轴分析，不自动把录音波形与 PCAP 精确对时，不据此确认根因。

## 5. 退出码与验证范围

- `0`：本步骤完成；不代表抓到了全部业务通话或已确定媒体归属。
- `2`：参数/配置、索引版本或依赖错误。
- `3`：某机/输入失败、抓包提前结束、索引限额、继承不完整索引，或 HOMER 部分/失败结果；读取已保存的清单和成功数据。

新增测试使用两台模拟 SSH 主机、两通同号码并发 SIP/SDP/RTP、真实本机 tshark，以及调用原 HOMER CLI 的本机 HTTP 模拟服务。它们验证并发、分片摘要、失败隔离、FS 字段白名单、会话检索、媒体筛选、桥接 Call-ID、HOMER 部分结果保留及报告接入。没有连接生产主机或真实 HOMER 实例。

1.0 历史验收：2026-09-19 基于当时 `main` 的独立 PR 工作树执行 156 项测试，全部通过；其中抓包、会话检索、HOMER 联动及报告新增 41 项，批量与会话联动占 19 项。实际 CLI 的 index → search → export → report 合成链路及主机清单离线计划通过。文档链接、示例配置、schema 和 HTML 本地资源检查通过。未进行浏览器/试听验收；此前本地文件访问被浏览器 URL 策略阻止。
