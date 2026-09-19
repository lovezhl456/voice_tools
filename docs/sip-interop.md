# SIP 分层互通验收与 HTML 用例说明

执行日期：2026-09-19。用户授权按 pjsua → Baresip → 公共服务实施，并要求参考 GitHub 测试、执行多个用例和交付 HTML。

**最终 20/20 个端到端用例通过：pjsua 9 个、Baresip 7 个、公网 4 个。** 另直接运行 Baresip 官方 6 个自测，6/6 通过。SIP 回归发现 28 项，其中 20 项执行通过、8 项旧 localhost 集成测试因本次未启用对应开关而跳过；不能把 28 项全部称为执行通过。

仓库版：[20 项用例 HTML](sip-cases/index.html) · [结果与来源摘要 JSON](sip-cases/results.json)。这是 2026-09-19 的历史实测摘要；包含前提、步骤、预期、逐项结果、指标及原始验收 JSON 摘要，不分发本机地址、Call-ID、录音或原始日志。运行 `python scripts/sip_case_catalog.py` 可离线重建 HTML。

完整证据版 HTML（仅原验证机器）：[打开本地报告](http://127.0.0.1:8080/voice-tools/sip-interop/)。离线文件：`outputs/sip-interop-report-20260919/index.html`，与相邻证据目录一同保留即可查看。人读说明和可试听音频在 HTML 中，机器结果在同目录 `results.json`、`manifest.json`。

## 用例清单

| ID | 场景 | 核心判断 |
|---|---|---|
| P01／P02 | pjsua PCMA／PCMU 回声 | 编码匹配；实际 RX 和对端录音均包含已知合成信号 |
| P03 | pjsua 双向不同 WAV | CLI 收到对端提示音，同时对端录到 CLI 发出的不同信号 |
| P04 | RFC4733 `11220*#` | 对端按键顺序、重复次数、传输方式一致 |
| P05 | SIP INFO `12*#` | 对端日志确认方法及完整字符序列 |
| P06 | 486 拒接 | CLI 退出 3，保留 SIP 486 |
| P07 | 不接听 | WAIT_TIMEOUT，发送 CANCEL 清理 |
| P08 | 远端提前挂断 | REMOTE_HANGUP，保留已收到的录音 |
| P09 | PCAP 导入后播放 | 实际音频匹配；RFC4733 重复结束包只产生一个按键 |
| B01／B02 | Baresip 双编码回声 | 独立 SIP 实现返回正确合成信号 |
| B03 | RFC4733 `11220*#` 往返 | CLI 收到与发送完全一致的事件序列 |
| B04 | SIP INFO 输入，RFC4733 回送 | 独立栈往返按键序列一致 |
| B05 | Baresip 双向 WAV／录音 | 两端分别收到预期的不同信号 |
| B06 | 编码无交集 | PCMA 主叫对 PCMU 被叫，拒绝协商而非假成功 |
| B07 | PCAP 跨栈复现 | 音频匹配、去重后的按键事件返回 |
| N01 | SIP2SIP 4444 | 公网 SIP 200；RX 信号匹配已发送素材 |
| N02 | SIP2SIP 3333 | 公网 SIP 200；实际接收有效音频 |
| N03 | IPTel echo | 公网 SIP 200；RX 信号匹配已发送素材 |
| N04 | IPTel music | 公网 SIP 200；实际接收有效音频 |

公网四通均未配置认证账号或 REGISTER，只调用官网公开测试分机。出站代理根据当次 UDP SRV 查询生成并记录，没有把后台 IP 固化成工具默认配置。这是本次匿名访问成功的证据，不是所有网络、所有时间均无需认证的承诺。

## 上游依据与本地实现

- PJSIP 2.17 的 `tests/pjsua/mod_call.py`：呼叫状态、按键完整序列与重复数字；`mod_media_playrec.py`：实际录音比对；`scripts-call-wav/600_playwav_basic.py`：WAV 播放；`scripts-sipp/uas-early-bye.xml`：早挂断。
- Baresip 固定提交 `f48c14bc35b55eb4443c627ef77f578a91e2a9ff` 的 `test/call.c`：接听、DTMF、拒接、取消；`test/play.c`、`test/ausrc.c`：音频样本与文件音源。
- 把上游断言改编为实际 CLI 外部验收，没有宣称完整运行 PJSIP／Baresip 上游套件。SIP INFO、PCAP 转换及公网短呼叫包含本项目扩展；具体来源链接与 SHA256 见 HTML。
- 直接运行的 Baresip 官方自测为 `test_play`、`test_ausrc`、`test_call_answer`、`test_call_dtmf`、`test_call_reject`、`test_call_cancel`，RTP RX 模式为 `main`。

执行器：[tests/sip/interop.py](../tests/sip/interop.py)。报告生成器：[scripts/sip_interop_report.py](../scripts/sip_interop_report.py)。波形断言反例：[tests/sip/test_interop_metrics.py](../tests/sip/test_interop_metrics.py)。生产 CLI 源码未因本轮测试修改。

## 本机测试端与复现

PJSIP 2.17 已有本地构建，pjsua 位于 `.local/sip-build/pjproject-2.17/pjsip-apps/bin/`。Baresip 4.11.0 与 libre 4.11.0 本轮构建安装到 `.local/sip-interop-build/prefix/`，没有全局安装或常驻服务。

Baresip 模块：`g711;aufile;aubridge;auconv;auresamp;ausine;echo;stdio;menu;account;ctrl_tcp;debug_cmd`。`ausine` 用于官方自测；`aufile` 的播放设备模式可把对端收到的音频写成 WAV，因此本轮不依赖 libsndfile。构建命令与源码下载摘要在 `.local/sip-interop-sources/`；编译使用现有项目 CMake 和本机 OpenSSL。

本地 echo 配置的关键项为 `sip_listen 127.0.0.1:PORT`、`net_interface 127.0.0.1`、`call_accept yes`、`audio_source aubridge,echo`、`audio_player aubridge,echo`，再加载 account／echo 应用模块；文件提示音模式使用 menu 和 aufile。每通用例启动独立进程和配置，结束后退出，不监听局域网。

在仓库根目录运行；输出目录必须是新的：

```bash
.venv/bin/python -m tests.sip.interop --phase pjsua --out outputs/NEW-pjsua
.venv/bin/python -m tests.sip.interop --phase baresip --out outputs/NEW-baresip
.venv/bin/python -m tests.sip.interop --phase public --out outputs/NEW-public
```

`--case P01` 等可只执行指定用例；重复传递可选多项。public 会真实拨打测试服务，每个用例一通，接通等待上限 12 秒、通话上限 15 秒、外部进程上限 40 秒，通话之间间隔 2 秒。它不会被普通 unittest discovery 自动触发。

换机器时可用环境变量 `VOICE_SIP_PJSUA` 指定 pjsua 二进制，`VOICE_SIP_BARESIP_PREFIX` 指定包含 `bin/baresip` 与 `lib/baresip/modules` 的安装前缀。本轮验证环境是 macOS arm64，未声称这些二进制可直接迁移其他平台。

```bash
.venv/bin/python scripts/sip_interop_report.py \
  --pjsua outputs/NEW-pjsua --baresip outputs/NEW-baresip \
  --public outputs/NEW-public --out outputs/NEW-report
```

完整报告可加 `--sources PATH` 指定上游测试来源快照和日志目录；默认使用 `.local/sip-interop-sources/`。缺失这些本机材料时，报告会标记未提供，不会把本次互通结果当作上游自测证据。仓库中的便携 HTML 摘要不依赖此目录。

## 波形断言与首次失败的处理

信号为确定性的合成扫频和包络，不是人声。以十段 100 ms 的参考片段检查收到的 PCM：至少 8 段相关系数 ≥ 0.65，中位数 ≥ 0.85，匹配偏移变化 ≤ 120 ms。匹配必须围绕同一播放周期；静音窗口不能放大 FFT 数值误差。反例测试覆盖静音、另一条扫频、过短音频；正例覆盖整体延迟、音量变化和小幅时钟调整。

首轮 1 秒整段比对受到 PJSIP 自适应抖动缓冲调整影响；随后发现循环提示音会匹配到不同周期。两次测量规则修正后固定为上述规则，并重新执行最终 pjsua／Baresip／公网用例，最终报告采用相同规则。这里验证媒体身份与基本连续性，不是 MOS／PESQ 或无损音频质量证明。

Baresip 首次未显式选回环地址，随后 echo 模式又缺少直接接受来电的配置，已通过官方源码定位后修改测试配置。官方自测首次因模块搜索目录缺少 `ausine.so` 中止，补齐链接后 6 项全部通过。所有初次失败、原录音和复测批次保留在报告的 attempts 目录与调试记录中。

## 尚未覆盖

本轮没有执行真实网关／机器人业务菜单、并发、长通话、TLS／SRTP、ICE／TURN 或公网认证测试。SIPp 原包直放仍受之前 raw socket 权限限制；P09／B07 使用的是 PCAP 转换路径。`tx_source.wav` 始终只是本地发送源重建，远端接收由回声或对端录音另行证明。
