# SIP 工具验证记录 · 2026-09-19

PR 整合复核：基于 `main` 的 `dda856c`，在独立工作树中执行全仓测试，**209 项全部通过**，已开启 `VOICE_TOOLS_SIP_LOOPBACK=1`，包含 8 项真实 localhost SIP 集成测试，没有跳过。该次只复测本地，不重拨公网；下面保留初始实现阶段的验证记录，分层互通见 [sip-interop.md](sip-interop.md)。

实现范围和操作步骤见 [人工手册](sip.md)，机器接口和维护规则见 [Agent 协议](sip-ai.md)。本次按用户确认，仅做本地回环，不连接外部网关。

## 已通过

- 完整仓库回归：**182 项通过**，耗时 43.076 秒。
- SIP 专属：**26 项通过**，其中 **8 项真实 localhost 集成测试**。
- 实际安装并运行两个入口：`voice-tools sip`、`voice-sip`；帮助、机器 JSON、运行时 schema、生成示例、校验和离线预演通过。
- PCAP 使用真实 tshark 解析合成抓包；验证单向流选择、G.711 已知数值、RTP 时间戳绕回／乱序、音频空洞、重复包、DTMF 去重、冲突和超限拒绝。
- 测试异常收尾、连接等待超时、提前挂断、素材快照一致性和录音后处理失败；失败不报告为成功。
- SIPp 选流导出、XML/计划生成、包摘要检查、拒绝任意 shell exec、预演与权限失败前置检查通过。

真实 UDP 回环使用独立的最小 SIP/RTP 应答端，没有 mock PJSUA2：

| 测试 | 实际核查 |
|---|---|
| 音频／按键／录音／挂断 | 对端收到非静音 RTP、RFC 4733 `1#`、SIP INFO `2`；本端收到非零 PCM 音频；对端收到 BYE |
| Digest | 本地 UAS 发出 401 challenge，核对计算出的摘要；结果和原生日志不包含测试密码 |
| REGISTER | 本地注册成功后执行呼叫 |
| 183 提前媒体 | 提前媒体启用时，录音早于策略开始超过 250 ms |
| PCAP 素材 | 导入的重复 DTMF 结束包在新通话中仅产生一次 `1` |
| 忙线 | 486 返回失败，保留状态码，不标记成功 |
| 对端提前挂断 | 播放期间 BYE，保存失败结果及已有录音 |
| 操作者中断 | 中断主 CLI，子进程收尾，对端收到 BYE，保留 interrupted 结果 |

## 可查看的演示产物

本机忽略目录 `outputs/sip-loopback-20260919/` 留存了一次合成回环，不含真人录音。该目录不随 Git 克隆分发。

| 观察值 | 结果 |
|---|---|
| 流程 | 合成 WAV → 等待 → PCAP 内容和按键回放 → `#` → 等待 → 挂断 |
| 对端实际按键 | `1`、`#`，与预期一致 |
| 对端收到的音频 RTP 包 | 31，其中 30 个含非静音载荷 |
| 对端收到 BYE | 是 |
| 接收录音 | `run/rx.wav`，2.04 秒 |
| 本地发送源重建 | `run/tx_source.wav`，明确不标为网络录音 |
| 机器证据 | `acceptance.json`、`run/result.json`、`run/events.jsonl` |
| 独立对端观察 | `peer/report.json`、`peer/peer.pcap` |

测试进程已结束，产物中的临时端口不是可持续使用的网关地址。复现应重新启动测试应答端，不应直接重拨历史配置。

## SIPp 直放的已知限制

本次构建了官方 v3.7.7 标签的 SIPp，启用 PCAP play，版本输出为 `v3.7.7-codeload-local-PCAP`。GitHub 标签归档只有占位版本头；本机构建仅补充对应版本字符串，没有修改媒体或信令实现。构建文件在 `.local/sip-build/`，不作为可移植发行包提交。

直接回放实测建立了本地 SIP 会话，但 SIPp 创建原始 UDP 套接字时失败：

```text
Can't create raw IPv4 socket (need to run as root?): Operation not permitted
```

该次 SIPp 退出码为 255，对端观察到 0 个音频 RTP 包；**不能宣称本机 SIPp 直接发包验证通过**。

因此增加 macOS 原始套接字权限前置检查：当前权限不足时，在发 INVITE 前以输入／运行环境错误退出，并给出 PJSUA2 内容回放替代路径。未自动 sudo、修改系统权限或增加 Linux capabilities。具备相应发包权限的部署环境仍需验证实际 RTP 直放及可选 live capture；本次未完成这两项成功路径验收。

## 环境与复现

- 本机：Darwin arm64，Python 3.9.6。
- PJSUA2：从官方 PJSIP 2.17 源码构建，安装在项目 `.venv`；原生包只在本机架构验证。
- tshark：使用本机 Wireshark CLI。
- 不安装 FreeSWITCH，不需要 GPU，不启动常驻后台服务。

```bash
source .venv/bin/activate
voice-sip doctor
VOICE_TOOLS_SIP_LOOPBACK=1 python -m unittest discover -s tests -v
```

测试环境需要允许绑定 `127.0.0.1`。无此权限的沙箱会阻止已有 HOMER 模拟服务器和 SIP 回环；这不等同于业务测试失败。本次完整回归是在允许本地套接字的执行环境完成。完整日志保存在忽略目录 `.local/sip-final-verification.log`。

待真实环境验证：外部网关路由、实际账号／代理、NAT／RTP 可达性、真实 codec/PT 协商、真实 IVR 业务结果、SIPp 特权发包及 live capture。现有测试不证明这些项已通过。
