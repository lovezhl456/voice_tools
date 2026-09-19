# SIP 深度检查与修复 · 2026-09-19

检查基线：PR #7 合并后的 `main`（`e6418369`）。范围包括输入校验、PCAP／DTMF 导入、PJSUA2 媒体连接、进程中断、失败分类与录音完整性。原 20 项互通验收是固定日期的历史记录；本次增加失败场景，不改写历史结果。

## 已复现并修复

| 优先级 | 问题与影响 | 修复和证据 |
|---|---|---|
| P1 | re-INVITE 更换 RTP 媒体端口后，录音与播放器仍连接旧媒体端口 | 真实 localhost 测试中，旧版接收 WAV 只有约 0.36 秒，播放步骤最终 `CALL_TIMEOUT`。切换时重新连接已有 recorder/player；修复后 2 秒播放继续双向传音，接收录音后半段有效，且没有重建或覆盖 WAV |
| P1 | 主 CLI 的 SIGTERM 未进入清理流程，可能留下仍在呼叫的 worker | 为 PJSUA2／SIPp 的父进程增加局部 SIGTERM 处理，退出时恢复原处理器；真实 PJSUA2 回环确认退出码 3、`interrupted`、对端收到 BYE |
| P2 | 读取通话详情或账号关闭失败，会跳过后续资源释放；清理错误会覆盖原通话错误 | 每个清理阶段独立执行，仍尝试销毁 Endpoint；保留首个错误，后续异常写入 `secondary_errors`。故障注入覆盖详情、账号关闭及日志写出失败 |
| P2 | worker 超时被归类为用户中断；损坏的 worker 结果会被当作输入错误 | 超时使用 `failed / WORKER_TIMEOUT`；无效结果使用 `WORKER_RESULT_INVALID`，原文件另存为 `worker-result.invalid.json` |
| P2 | RX WAV 只检查头部帧数，非空但截断的文件可能仍返回成功 | 逐块核验实际 PCM 数据长度、声道、采样率和位宽；截断文件返回 `RECORDING_INCOMPLETE` |
| P2 | 错误 JSON 中的数组／对象 action、超大整数或布尔 IP 地址绕过正常错误契约 | 校验前显式检查类型；CLI 退出 2，输出单个 JSON 错误，不打印 Python Traceback |
| P2 | PCAP 中超过 8 秒的单键或超过 256 个按键能够导出，但生成的媒体清单随后无法加载 | 导入与媒体清单加载共用同一按键校验；不支持的素材在写出前拒绝，并检查 `end_observed` 类型 |

新增回归见 [test_regressions.py](../tests/sip/test_regressions.py)。真实 UDP 场景见 [test_loopback.py](../tests/sip/test_loopback.py)，包括新增的 re-INVITE、SIGTERM、REGISTER Digest。REGISTER Digest 在本次测试中正常通过，未发现需要修改的注册逻辑。

## 验证记录

**全仓 223/223 测试通过，无跳过**：含 11 项真实 localhost 测试和 11 项新增故障／边界单元回归。原先 209 项中的 8 项 localhost，加上本次 3 项真实回环与 11 项边界回归，合计 223 项。原始日志保留在本次工作树的忽略目录 `.local/`，合成互通素材保留在 `outputs/`；不分发本机网络标识或原生二进制。

- pjsua：**9/9 通过**，覆盖 PCMA／PCMU、双向 WAV、两种按键、PCAP 转换及失败清理。
- Baresip：**2/7 通过，5/7 未通过波形断言**。失败项为 B01、B02、B03、B05、B07；信令、CLI 执行和按键相关检查通过，但不能据此把整个用例算作通过。
- 用与基线一致的旧版 PJSUA2 适配源码复测 B01／B02，两项同样失败，相关系数中位数约 0.816／0.779；因此没有证据把失败归因于本次修复。旧版适配源码 SHA256 已核对。
- 当前失败日志出现 `tx aubuf underrun`。2.3 秒预热实验中 B01 通过（中位数约 0.921），B05 仍失败（约 0.380）；**根因未定**，没有修改 0.85 的中位数阈值或移除失败批次。
- 另做 48 组离线 JSON 类型探测，未发现未处理异常；人工／Agent／检查文档的本地链接有效。

完整的可发布结果摘要及原始证据 SHA256 见 [sip-deep-review-results.json](sip-deep-review-results.json)。Baresip 音频稳定性是当前未解决的验收项；本次没有声称独立栈全部通过。

```bash
VOICE_TOOLS_SIP_LOOPBACK=1 python -m unittest discover -s tests -t . -v
python -m tests.sip.interop --phase pjsua --out outputs/NEW-deep-pjsua
python -m tests.sip.interop --phase baresip --out outputs/NEW-deep-baresip
```

本次不重复拨打公共服务。SIGTERM 验证不包含不可捕获的 SIGKILL；若应用在非主线程直接调用库函数，库不会修改全局信号处理器。SIPp 复用了父进程信号处理，但原包直放仍未在满足 raw socket 权限的环境验收。真实网关、IVR 业务语义、并发、长通话、TLS／SRTP 和 NAT 稳定性仍待各自验证。
