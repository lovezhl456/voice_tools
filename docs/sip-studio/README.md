# SIP Case Studio：可视化拨测工作台

2026-09-19 · 交付阶段：可交互本地原型，配套现有 `voice-tools sip` / `voice-sip`。

打开 [工作台](index.html) 或 [产品设计与社区调研](design.html)。本机入口：<http://127.0.0.1:8080/voice-tools/sip-studio/>。代码无构建步骤、无 CDN，支持静态 HTTP；也可直接打开 index.html，但浏览器的 file-origin 存储行为存在差异，以 HTTP 为验收入口。

## 这版可以做什么

- 左侧拖入等待、录音播放、DTMF、PCAP 媒体步骤；拖到步骤间隙排序，或使用 ↑ ↓。挂断固定在最后。
- 选中步骤，在右侧编辑名称、时长、按键、发送方式、素材路径；支持复制、删除、撤销和重做。
- 用例库支持搜索、创建、复制、删除、录音模板、导入 JSON。模板中的音频路径需要自行准备素材。
- 环境库管理目标 URI、账号、UDP 端口、PCMA/PCMU、超时、early media、注册、代理、IPv4 与认证环境变量名。
- 素材库读取本机 WAV，检查 RIFF/PCM16/8 kHz/单声道，显示实际样本幅度并试听；也可登记 CLI 已转换的 media.json。
- 字段检查、动作预算、离线时序预演，以及两种文件导出。

**离线预演不会发起 SIP 呼叫，也不会生成录音。** 真实拨测按钮明确显示未连接。运行记录中的真实执行、媒体验证、业务断言初始均为空；离线预演仅保留在当前页面会话。

## 五分钟上手

1. 默认「IVR 按键导航」不引用音频文件，可以直接导出。它等待 2 秒、发送 `1#`、等待 3 秒、挂断；默认目标是 localhost 占位参数，需要另行启动被叫端。
2. 从左侧点击/拖入一个步骤。点击卡片后在右侧编辑；拖动卡片到步骤间隙改变次序。手机可点击插件，再用卡片 ↑ ↓ 调整。
3. 「用例设置」修改名称、标签和环境；「环境」修改网关参数。多个用例引用同一环境时，共享后续参数修改。
4. 「离线预演」检查步骤顺序和已知动作时长。接通等待不计入此动作时间轴；未知媒体时长后面的绝对起点显示待确认。
5. 「导出用例」下载 `scenario.json`，按相对路径准备素材后运行下列离线命令：

```bash
voice-tools --json sip validate scenario.json
voice-tools --json sip run scenario.json --dry-run --out outputs/plan-001
```

使用新的输出目录。通过 CLI 校验并准备好目标后，可在终端去掉 `--dry-run` 实际拨测。浏览器原型不执行这一步。

## 草稿、执行文件和素材

| 文件/对象 | 保存内容 | 用途 |
| --- | --- | --- |
| `case.studio.json` | `studio_version=1.0`、名称、标签、环境快照、步骤 ID/名称/参数 | 保存编辑信息，可重新导入 |
| `scenario.json` | `schema_version=1.0` 和当前 CLI 支持的执行字段 | 给现有 CLI 校验和执行 |
| 浏览器本地存储 | 多个用例、共享环境、素材元数据；只保存字段有效的草稿 | 当前浏览器恢复，不是跨设备备份 |
| 本机会话内存 | WAV object URL、待登记的 File、离线预演记录、撤销历史 | 页面关闭/刷新后消失 |
| 素材文件 | 由用户放置在导出用例对应目录 | 不随 JSON 下载，不自动复制 |

路径相对于 scenario.json 所在目录；media.json 中的音频路径相对于 media.json。典型目录：

```text
case-001/
  scenario.json
  case.studio.json
  assets/question.wav
  media/media.json
  media/audio.wav
```

没有素材时长时，仅计算已知动作时长，**不能把字段通过当作 CLI validate 通过**。登记的时长只是浏览器元数据；真实文件、SHA256、完整时长与音频可读性由 CLI 再次检查。

同路径重新添加素材会替换索引；移除索引不会删除本机文件或改写步骤。WAV 最大 16 MiB，JSON 最大 1 MiB，最多 100 个用例、100 个素材索引。有效编辑自动保存；参数无效时暂不覆盖最近一次有效存档，修正后继续保存。存储损坏时保留原始数据，提供恢复文件下载并临时停止覆盖。

## PCAP 的正确入口

原型接收已转换的媒体清单，不直接解码任意 PCAP。先使用 `pcap-inspect`，明确选择单方向、单 SSRC，再 `pcap-import`。动态 G.711 payload type 和 DTMF 映射按实际捕获与 CLI 参数填写；不要猜测。生成 `media.json + audio.wav` 后登记清单，使用 `play_media`。

它复现转换后的音频和按键时序，不复现原始 RTP 包头、丢包或网络抖动。需要原包回放时仍由独立 SIPp 测试执行。

## 录音与结果边界

录音是通话级行为：自动 RX 录音，可选择 early media；没有虚构的「开始录音/结束录音」步骤。后续结果页应分别呈现：

- `result.json`：执行完成/失败/中断，不等同 IVR 业务正确。
- `rx.wav`：真实收到的解码音频。
- `tx_source.wav`：本地播放源时序重建，不是发送 RTP 抓包或远端接收证据。
- `events.jsonl`：步骤/通话事件；业务断言当前为 `not_evaluated`。

未接入 ASR、条件分支、并发活动、在线插件安装或真实执行服务；这些能力不写入当前 CLI JSON。

## 文件与检查

- [设计说明](design.md)：社区证据、交互与分期架构。
- [AI 维护协议](ai-contract.md)：字段边界、插件契约、验收规则。
- [验收记录](acceptance.md)：本次实际测试与限制。
- [来源账本](sources.json)：官方 URL、检索日期、GitHub commit 与内容 hash。

```bash
node --test tests/studio/core.test.cjs
PYTHONPATH=src python3 tests/studio/check_cli_compat.py
```

这些命令不发 SIP。UI 变更另需验证实际浏览器的拖拽、侧栏编辑、导入导出、刷新恢复、素材与手机导航。

导入 Studio 草稿与恢复本浏览器存档时，可选运行参数会按 CLI 默认值补齐；不自动追加导入文件里没有的挂断步骤。
