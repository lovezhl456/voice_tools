# 抓包命令示例

完整、可复制的 UUID 查询、手工端点、SCP 恢复和多文件报告命令见 [抓包与报告指南](../../docs/capture-report.md)。示例域名、UUID、IP 均为占位值，替换为自己有权限操作的目标。

无需主机的本地预览：

```bash
voice-tools capture start --host example-fs \
  --flow 192.0.2.1 16000 192.0.2.2 24000 \
  --dry-run --out outputs/example-plan
```

此命令只生成 `capture.json`，不连接网络。离线报告的合成 RTP/WAV 构造器位于 `tests/report/fixtures.py`；不在仓库保存真实录音或 PCAP。

2.0 环形/ESL 示例见 [hosts.esl.example.json](hosts.esl.example.json) 和 [完整操作指南](../../docs/capture-v2.md)。密码仅存远端 file/env；示例不包含密码。可先运行 `voice-tools capture ring-start --inventory examples/capture/hosts.esl.example.json --dry-run --out outputs/ring-plan` 离线检查。
