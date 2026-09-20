# 0.9.1 批量与 SIPp 验收

2026-09-19 · 分支 `feat/v0.9.1-sip-batch-sipp`。开发验证完成不等于已合并 / 已发布，也不代表生产容量已验证。

## 已验证

- 完整 Python 回归：共 284 项，其中 273 项通过，11 项因既有原生 / 外部环境条件跳过。最初沙箱禁止两个旧 HTTP 服务测试绑定端口；允许本机回环后完整重跑通过。
- 随后的受影响范围复验：42 项通过；最终新增批量 / SIPp 用例集 14 项通过。涵盖并发上限、失败后继续、真实子进程清理、中断与取消、离线不拨号、全队列预检、端口池边界、旧结果保留、XML / PCAP 篡改拒绝及统计完整性。
- Studio 核心：22 项通过；现有 CLI 兼容性 9 个场景通过（16 次成功命令和 1 次预期的素材缺失拒绝）。
- 浏览器编译器与 Python 编译器：SIP INFO / RFC4733 XML 逐字节一致；重复键、`#`、A–D、40ms 边界的 PCAP 字节一致；独立 tshark 解码验证 payload type 101、重复键 11#、160ms 持续时间及 3 次结束包；ZIP 校验和正确，实际浏览器下载的包通过 CLI dry-run。
- Chrome 实际界面：1440×1000 桌面和 375×812 手机，空队列、多项、增加 / 删除 / 排序、重复次数、刷新恢复、后端切换、并发非法值、跨网关拒绝、真实 JSON / ZIP 下载、80 项长文本结果与 HTML 转义、结果滚动、键盘焦点 / Enter 操作、SIPp 文档滚动与返回入口。页面无横向溢出、浏览器错误为 0；手机结果表格使用内部横向滚动。
- 本机总导航 → voice_tools → SIP Case Studio 可达；工作台与六个相关资源 / 文档均 HTTP 200。没有创建云站点。

## 原生回环测试

环境：macOS；既有 PJSUA2 原生绑定；`SIPp v3.7.7-codeload-local-PCAP`。仅发送到 `127.0.0.1`。脚本：[sip_batch_smoke.py](../scripts/sip_batch_smoke.py)。

| 测试 | 参数 | 结果 |
| --- | --- | --- |
| 功能批量 → 独立 SIPp UAS | 4 通，并发 2，PCMU | 4 完成、0 失败，逐通录音 / 事件 / 结果落盘 |
| SIPp 信令压力 → SIPp UAS | 4 通，并发上限 2，4 CPS | created=4、successful=4、failed=0、active=0，退出 0 |
| SIPp SIP INFO → 自定义 UAS | 同上，每通 1 个按键 | 4 成功、0 失败，INFO 获得 200 |
| RFC4733 PCAP 回放 | 同上 | 未通过：macOS 当前账号不能创建原始 IPv4 套接字；记录 failed / 255，未标记完成 |

RFC4733 原始错误：`Can't create raw IPv4 socket (need to run as root?): Operation not permitted`。未使用 sudo 或修改系统权限；媒体实际发出 / 被远端收到仍待在具备权限的测试环境验收。PCAP 文件生成、包结构、按键时序和跨编译器一致性已离线验证，不能替代发包验证。

回环测试曾发现测试夹具的 SIPp UAS 默认只提供 PCMU，而用例默认要求 PCMA；将夹具用例改成 PCMU 后复验通过。代码没有通过放宽结果判定绕过这一失败。SIPp `-sd uas` 返回 99 属于输出内置场景后退出，脚本已按此语义处理。

## 实现边界

- 工作台仍为静态本地界面：编辑、导出和导入结果；真实运行由 CLI 启动，导入结果不自动刷新。
- 功能队列复用现有 PJSUA2 全步骤，逐通隔离。SIPp 首版压力导出覆盖等待、两种按键和挂断；WAV、media.json、认证、注册、代理、接收录音或业务断言不会被悄悄丢弃，而是阻止压力导出。
- SIPp 统计必须证明全部请求呼叫完成，退出 0 且统计缺失 / 总量不完整也判为失败。超时 / 中断保留已有证据。
- 未验证生产网络 / NAT、真实终端、不同 Linux 发行版安装、高并发容量、连续语音带宽压力或实体 iPhone / Safari。
- 原有 `sipp-prepare` / `sipp-run` 单通话原包回放保持原路径。

本机原始日志、截图保存在工作树忽略目录 `.local/batch-validation/`。仓库中保留测试代码、复现脚本和精简结果 [sip-batch-validation.json](sip-batch-validation.json)。

## 2026-09-20 · 合入 main 后复验

- 主线基线：`d44599c`（PR #14 的 SIP 断言，包含 PR #15 的 sngrep 文档）。保留两侧文档和功能，源码 / CLI / schema 继续使用 0.9.1。
- 全仓启用本机 SIP 集成测试：`VOICE_TOOLS_SIP_LOOPBACK=1 PYTHONPATH=src python -m unittest discover -s tests -v`，315 项全部通过，无跳过，包含主线的 19 项原生 SIP 测试。
- Studio 核心 22 项通过；CLI 兼容 9 个场景通过。此轮没有修改 HTML / JS / CSS，保留前轮 UI 验收范围。
- 新增两个集成边界测试：功能队列逐项保留断言；SIPp 明确拒绝非空功能断言，不静默删除。
- 另行执行两通真实回环呼叫、并发 2：预期 200 的任务通过，故意预期 486 的任务在呼叫正常完成后因断言不匹配而失败，整批结果正确为 failed。
- 重新生成 schema，核对批量命令与 sip_assertions=1.0 合同同时存在；冲突标记、版本一致性及相关文档链接检查通过。
