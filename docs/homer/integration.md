# HOMER 整合来源与验证边界

[返回工具导航](../../README.md) · [当前完整手册](../homer.md)

2026-09-19 将用户此前提供的 `homer7-cli-1.0.0.zip` 纳入 voice_tools。原包是针对 HOMER 7 Web API 的独立 Python 客户端，不包含 HOMER 服务端代码。

原 ZIP 的 SHA-256：`f33f2d196aeea9fb57e3ee5d1560201d412c7647725d90883df0ff60b17fd25c`。

## 迁移范围

- `homerctl.py` → [client.py](../../src/voice_tools/tools/homer/client.py)：原查询、认证、分析、导出逻辑保留；只给 `build_parser` / `main` 增加可选 `prog`，使统一入口的帮助显示 `voice-tools homer`。
- 新增 [cli.py](../../src/voice_tools/tools/homer/cli.py) 适配和工具注册，完整转交原始参数，沿用 JSON 错误与退出码。安装时提供 `homerctl` 兼容命令。
- 原客户端测试迁至 [tests/homer/test_client.py](../../tests/homer/test_client.py)，调整导入和 fixture 路径；模拟 HTTP 测试改由 `python -m voice_tools homer` 实际运行。
- 合成 trace、预期分析示例和 AI 调用器迁至 [examples/homer/](../../examples/homer)。AI 调用器改为通过当前解释器调用已安装模块，保留参数数组、命令白名单和操作者配置边界。
- Markdown 手册完整保留 11 章业务说明，更新安装、命令和路径；原 HTML 手册作为历史资料保留。
- 配置与缓存不迁移、不改写，继续读取原 `homerctl` 路径。原项目和 ZIP 不作修改。

## 原包文件校验值

下列值对应迁移前的原文件，用于追溯来源；经过入口或路径适配的新文件不会具有相同校验值。

| 原文件 | SHA-256 |
|---|---|
| `homerctl.py` | `c1b5d66c1e98c87e725db51df2b581b6ef33985ec5e59d441cca2e84aedeed41` |
| `tests/test_cli.py` | `006d2b707f42322bcf16081a0ed30f607d9d96802d37b4c783470e9735fd5f36` |
| `MANUAL.zh-CN.md` | `dbc78739cb5407ea34eb0ece1f4f88d46a3e18ac840364e62b976080639ed8ec` |
| `MANUAL.zh-CN.html` | `aff488443e05ace190638f9ead8080c4a8410f13f89d75d03feaa5aaf4cdbd67` |
| `SOURCE_BASELINE.md` | `0072797465c7c9f972a1813da598550683a4f6c0d56d818f2f34423e61ffeb30` |
| `TEST_REPORT.md` | `49d81ba4f924e170d5537120e871a418374d2ccd8b38164689edbe7b36748ff5` |

## 保留的历史资料

- [SOURCE_BASELINE.md](SOURCE_BASELINE.md)：2026-09-15 固定的 HOMER 服务端提交、HTTP 路由及语义依据，原文保留。
- [TEST_REPORT.md](TEST_REPORT.md)：原独立包在 Linux / Python 3.12 的历史测试报告，原文保留。其日期和测试数量不代表本次整合的运行结果。
- [MANUAL.zh-CN.html](MANUAL.zh-CN.html)：原始完整 HTML 手册，原文保留。使用其中旧的 `python3 homerctl.py` 示例时，替换为 `voice-tools homer`；新路径以[当前手册](../homer.md)为准。

当前回归通过仓库根目录的 `python -m unittest discover -s tests -v` 运行。测试包含合成录音和本机模拟 HOMER 服务，不使用线上凭据、不连接生产 HOMER。真实实例仍需按手册第 10 章核对认证、字段映射、已知通话及导出内容；不据模拟测试声称适配所有版本。

2026-09-19 在 macOS arm64 / Python 3.9.6 完成本次整合验证：93 项测试全部通过（含已安装的 WebRTC VAD 测试）；实际安装后，从仓库之外运行 `voice-tools homer` 与 `homerctl`，schema 和离线分析结果一致。另验证无 site-packages 时可执行 HOMER schema、文档本地链接和原资料校验值。此记录不替代上述真实实例验收。
