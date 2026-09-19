# 公共 SIP 测试服务与本地轻量测试端

核查日期：2026-09-19。用途：为 `voice-sip` 的少量、脚本化 IVR／语音机器人测试选择对端。

后续实施已完成：[分层互通验收](sip-interop.md)。SIP2SIP／IPTel 四个测试分机均已在本次网络实际拨测通过。本文保留调查时的证据边界，实际结果、日期、限制和复现方法以验收文档及 HTML 为准。

**建议：公网先选 SIP2SIP／IPTel 做连通性与音频检查；本地先复用已构建的 pjsua，增加独立协议栈验证时选 Baresip；SIPp 负责信令场景与 RTP 包回声。** 需要“按 1 播 A、按 2 播 B”的确定性菜单时，再做一个小型可编程 IVR 测试端。

本次完成官方网页、DNS、源码和本机 CLI 帮助核查；没有拨打公共号码、注册账号、安装新软件或启动新服务。下列示例命令没有进行通话验收，不能沿用此前自制 loopback peer 的测试结果。

## 1. 免费公共测试服务

“免费托管服务”和“开源软件”是两个维度。下列站点使用或来自开源通信生态，但这不代表整套托管配置、运营状态和服务保障全部开源。公开测试分机也不意味着允许批量压测。

| 服务 | 官方测试 URI | 能测试什么 | UDP／账号与现状 | 判断 |
|---|---|---|---|---|
| **SIP2SIP** | `sip:3333@sip2sip.info`；`sip:4444@sip2sip.info` | 3333 音视频测试；4444 麦克风／音频回声 | 官网明确免费，提供免费账号；配置文档明确 UDP 5060，当前 UDP SRV 也存在。账号要求见下文 | **公网首选候选**；官网当前提供测试文档和状态页 |
| **IPTel** | `sip:music@iptel.org`；`sip:echo@iptel.org` | 音频公告／音乐；自身语音回声 | 官网明确免费 SIP 账号和测试 URI；当前 UDP SRV 指向 `sip.iptel.org:5060` | **公网备选候选**；官网声明服务按现状提供，不保证随时可用 |
| **SIP5060** | `sip:test.time@sip5060.net`；`sip:test.dtmf@sip5060.net`；`sip:test.echo@sip5060.net`；`sip:test.ring@sip5060.net` | 报时；按键后按 `#`，语音读回号码并挂断；回声；只振铃不接听 | 测试页公开列出号码；站点跨域接入文档要求双向 TLS，当前未发现 UDP SRV，TLS SRV 为 443 | **当前 UDP 版不推荐直接接入**；测试分机是否有额外 UDP 例外未获证实 |

### SIP2SIP：资料与状态最完整

- [官网](https://sip2sip.info/)明确服务免费，并提供免费账号。这里的建议只涉及 SIP 测试分机；官网另有收费 PSTN 外呼，不能混为一谈。
- [帮助页](https://sip2sip.info/help/)直接公布 3333 和 4444。**没有在所查文档找到 DTMF 识别、读回按键或可定制 IVR 菜单的承诺**。
- [设备配置](https://sip2sip.info/settings/)要求按 RFC 3263 使用 DNS NAPTR／SRV 发现服务器，不应长期写死后台 IP／主机／传输配置。文档列出的 UDP 5060 只是说明；此次 DNS 与文档一致。
- 账号凭据用于认证和授权；官网说接收入站呼叫需要 REGISTER。文档没有明确承诺“任意匿名第三方均可直拨测试分机”，因此**匿名访问是否允许、测试呼叫是否要求账号尚待实拨验证**；不要把“公开号码”等同于“免认证”。
- [状态页](https://sip2sip.info/status/)在 2026-09-19 07:20 UTC 显示 `All Systems Operational`，SIP 和 DNS 显示 Operational。这是运营方状态声明，不是从本机打通的证据。
- 旧 `wiki.sip2sip.info` 路径在此次环境解析失败；上述新官网页面可用，应优先引用新地址。

### IPTel：简单音频测试入口

- [官网 FAQ](https://www.iptel.org/#faq)明确公布 `music` 和 `echo`，推荐 registrar／outbound proxy `sip.iptel.org`，默认端口 5060。
- 官网说明使用 Kamailio／SER 等开源实现，账号免费，不提供自身的 PSTN 落地服务。
- 此次 DNS 查询到 `_sip._udp.iptel.org` 的 5060 SRV。网页页脚仍为 2021，也没有取得独立的实时通话健康数据；因此保留为备选，**不宣称已确认现网可用**。
- 所查官网没有明确说明匿名跨域直拨测试号码的认证规则，也没有 DTMF 读回测试说明。

### SIP5060：有 DTMF 测试，但接入条件不匹配

[测试页](https://sip5060.net/test-calls/)最接近“发送按键并检查对端识别”的需求，且说明测试由 Asterisk 驱动。然而[接入规则](https://sip5060.net/calling-sip5060-users/)明确要求 TLS 客户端证书，而当前 DNS 只发现 SIP TLS 服务，没有 UDP SRV。

测试页所说支持加密／不加密的 AVP、AVPF 是**媒体配置**，不能据此推导其 SIP 信令允许 UDP。接入文档讨论普通用户，是否同样适用于所有测试分机尚不明确；不能断言 UDP 永远不可用，也不能推荐为已确认的 UDP 测试端。页面还引用较老的浏览器和证书服务，服务现状需进一步实拨核查。

### 查过但不作为现成测试号码推荐

- [Linphone](https://www.linphone.org/en/)：当前官网仍提供[免费 SIP 账号入口](https://subscribe.linphone.org/register/email)，适合自备另一端做账号间互通；本次所查页面没有找到足够明确的公开 echo／DTMF 测试号码证据。
- [Antisip](https://sip.antisip.com/service/faq.html)：免费 VoIP 服务，需创建账号；主要提供注册、路由、语音信箱。适合自备对端，本次未找到官方公开 echo／按键读回分机。
- [Testcall.com](https://testcall.com/)：以商业监测、PSTN 测试和容量测试服务为主，本次未确认符合“免费、直接 SIP UDP 媒体测试端”的入口。

这些结论是本次检索范围内的发现，不能解释为所有服务永久没有这些能力。

## 2. 本地轻量方案

这里的“轻量”按依赖和部署方式比较，没有测量各方案的 RAM／CPU，避免虚构资源数字。前三项都是已有开源程序，不需要 FreeSWITCH＋ESL。

| 方案 | 对端能力 | DTMF／IVR | 录音 | 代价与适合用途 |
|---|---|---|---|---|
| **pjsua CLI** | 自动接听；解码后音频回送；WAV 播放；正常 SIP 通话 | 日志可观察 RFC2833／SIP INFO 按键；按键菜单要另写逻辑 | `--rec-file`＋`--auto-rec` | **当前最省事**：本机已构建。同属 PJSIP 栈，不能作为独立实现互通的全部证据 |
| **Baresip** | 完整独立 SIP UA；`echo`＋`aubridge`；`aufile` 文件音源 | echo 源码接收并回送 DTMF；菜单分支仍需控制器／模块 | `sndfile` 输出 enc／dec 应用音频 | **独立协议栈首选候选**；需另装／构建模块。echo 源码明确标记 experimental |
| **SIPp UAS＋RTP echo** | 可控 SIP 响应和时序；直接回送收到的 RTP／UDP 包 | 默认 UAS 没有电话事件协商；SIP INFO 可写 XML 接收和匹配；不内建音频菜单判断 | 不会自动生成对端接收 WAV；现有 CLI 可录 RX，也可另抓包 | **信令与包路径测试合适**；本机已有二进制。不是完整媒体解码型 IVR |
| **Diago 小型 Go 服务** | 官方库提供呼入处理、媒体、播放和 DTMF 读写能力 | 可编写固定菜单、超时、按键断言、故障注入 | 可按测试设计接入媒体记录 | **需要确定性多轮 IVR 时再选**；它是开发库，不是零配置现成测试服务 |

现有 `tests/sip/loopback_peer.py` 继续承担精确协议断言与 CI 回归即可。它是有限的自制 UAS；pjsua／Baresip 可以补充社区实现互通，公网服务补充实际网络路径。三者证据互补。

### A. pjsua：最快得到会接电话、播放和录音的对端

本机已核对 PJSIP 2.17 可执行文件的 `--help` 与源码，确认下列参数存在。**以下为待实测命令，不是本轮执行记录。** 在仓库根目录、交互终端运行，每次使用新的输出目录：

```bash
PJSUA=.local/sip-build/pjproject-2.17/pjsip-apps/bin/pjsua-aarch64-apple-darwin25.6.0
PEER_OUT=$(mktemp -d /tmp/voice-sip-peer.XXXXXX)
"$PJSUA" \
  --id=sip:echo@127.0.0.1:5070 \
  --bound-addr=127.0.0.1 --ip-addr=127.0.0.1 \
  --local-port=5070 --rtp-port=6000 --no-tcp \
  --null-audio --auto-answer=200 --auto-loop --no-vad \
  --max-calls=1 --duration=30 \
  --rec-file="$PEER_OUT/peer.wav" --auto-rec \
  --log-file="$PEER_OUT/peer.log"
```

将 `voice-sip` 的 target 配为 `sip:echo@127.0.0.1:5070`，客户端另用 SIP 端口和 RTP 4000 等不冲突端口；不需要 REGISTER。接通后播放合成音频，检查客户端 `rx.wav` 是否收到回声，再检查对端日志中的 `Incoming DTMF`。

要测试对端播放提示音，把 `--auto-loop` 换成 `--play-file=/实际路径/prompt.wav --auto-play`。`--auto-play-hangup` 可在播放完成后挂断。菜单分支不是这些开关自动提供的功能。

`--duration=30` 限制通话时长，不会在 30 秒后自动结束待机进程。测试完用交互命令 `q` 正常退出，保证 WAV 文件头完成写入。此录音位于应用音频路径，不是原始 RTP 抓包。

这里的 echo 通过 PJSIP conference bridge 将解码音频接回发送端，**不会原样保留 RTP 的序号、时间戳和包时序**。需要验证包回放时，应区分它与 SIPp 的包回声。

依据：[官方 CLI 手册](https://docs.pjsip.org/en/latest/specific-guides/other/cli_cmd.html)、[录音说明](https://docs.pjsip.org/en/latest/specific-guides/audio-troubleshooting/problems/how_to_record.html)、[2.17 参数源码](https://github.com/pjsip/pjproject/blob/2.17/pjsip-apps/src/pjsua/pjsua_app_config.c)、[媒体连接与 DTMF 回调](https://github.com/pjsip/pjproject/blob/2.17/pjsip-apps/src/pjsua/pjsua_app.c)。

### B. Baresip：增加独立实现互通

当前官方源码确认：

- `echo` 模块自动接听并回送媒体，依赖 `aubridge`；其 DTMF 回调调用 `call_send_digit()`，可以构造发送／接收按键事件的往返检查。源码标记 experimental，需先验证，不能把模块存在当作稳定性保证。
- 普通 UA 自动接听由 `menu` 模块实现账户参数 `answermode=auto`；echo 模块自身也处理来电，两种模式应分开配置和验证，避免重复接听。
- `aufile` 是文件音源，`sndfile` 在编码／解码过滤器处输出 enc／dec 音频。它们不是发送／接收网络包的原样归档。
- `ctrl_tcp` 可供外部程序控制和订阅事件；按键触发播放等复杂策略仍须开发，而不是配置一个开关即可完成。

建议只构建 G.711、所需音频与控制模块，使用独立配置目录。此次没有安装 Baresip，也未确认当前机器已有可用模块，不提供未经验证的整套启动命令。

依据：[项目模块清单](https://github.com/baresip/baresip)、[账户配置](https://github.com/baresip/baresip/wiki/Accounts)、[固定版本 echo 源码](https://github.com/baresip/baresip/blob/f48c14bc35b55eb4443c627ef77f578a91e2a9ff/modules/echo/echo.c)、[录音源码](https://github.com/baresip/baresip/blob/f48c14bc35b55eb4443c627ef77f578a91e2a9ff/modules/sndfile/sndfile.c)。

### C. SIPp：无需 raw socket 的 RTP echo

已核对本机 SIPp v3.7.7 的 `-h`、`-sd uas` 和源码。参考命令，尚未实测：

```bash
.local/sip-build/sipp-build/sipp \
  -sn uas -t u1 -i 127.0.0.1 -bind_local -p 5072 \
  -ci 127.0.0.1 \
  -mi 127.0.0.1 -min_rtp_port 6200 -max_rtp_port 6210 \
  -rtp_echo -m 1 -timeout 60s
```

客户端 target 为 `sip:echo@127.0.0.1:5072`。**该版本内置 UAS 的 SDP 只声明 PCMU／PT 0，所以客户端必须选 `codec: "PCMU"`，不能沿用默认 PCMA。** 默认 SDP 也不声明 `telephone-event`，不能直接拿来做 RFC4733 验收；需要自定义 XML 和正确 SDP。SIP INFO 的接收／响应也应显式写入场景。

`-rtp_echo` 使用 `SOCK_DGRAM` 接收后 `sendto()` 回送，没有 PCAP replay 的 `SOCK_RAW` 要求。因此，之前 macOS 的原包回放权限失败**不能推导出 RTP echo 也需要 root**；本地监听仍受运行环境的网络沙箱约束。

回送包不等于正确解码／理解按键。它适合检查包是否回来、信令是否符合预期；音频质量、DTMF 含义和业务菜单需另外断言。SIPp 原包回放仍保留此前的权限限制，本次未改变任何权限。

依据：[官方媒体说明](https://sipp.readthedocs.io/en/latest/media.html)、[v3.7.7 RTP echo 与 UDP socket 源码](https://github.com/SIPp/sipp/blob/v3.7.7/src/sipp.cpp)、[PCAP raw socket 路径](https://github.com/SIPp/sipp/blob/v3.7.7/src/send_packets.c)。

### D. 需要完整小型 IVR 时

若下一阶段目标是“播欢迎词 → 收到 1 播 A → 收到 2 播 B → 超时播提示 → 保存对端证据”，推荐评估 [Diago](https://github.com/emiago/diago) 的小型 Go UAS，或在 Baresip 上做控制器。Diago 官方说明有服务端呼叫处理、媒体播放及 DTMF reader／writer，适合作为可编程测试夹具。

这是新增开发工作。本次未编译或验证 Diago；当前 README 也明确媒体 API 正在调整，实施时需固定版本并核对示例，不能直接承诺现成示例即可使用。继续用 PJSUA2 写 UAS 也可复用 Python 环境，但仍然是同一 SIP 实现。

## 3. 对现有 CLI 的影响与验证顺序

1. **不需改变架构。** 当前 CLI 已有 UDP、PCMA／PCMU、账号认证、可选注册和代理；本地 pjsua、Baresip、SIPp 可以作为另一端直接接入。
2. **公网前先补齐／确认 DNS 发现。** 当前 `pjsua.py` 没有显式配置 PJSIP nameserver，也未暴露 DNS SRV 选项。SIP2SIP 明确要求 RFC3263 发现；要核对运行时解析路径，必要时增加配置或按当次 DNS 查询生成临时路由，不能把一次查到的后台地址固化成长期默认值。
3. **注册成功不代表双向 RTP 正常。** 当前 CLI 没有暴露 STUN／TURN／ICE；`public_address` 只改变通告地址，不会配置 NAT。SIP2SIP 文档描述了服务器侧 NAT 处理，但具体网络是否成功仍需实测，不应随意更改路由器或系统网络配置。
4. **公共 echo 只做少量连通／媒体检查。** 它不能证明 DTMF 菜单正确，不能上传自定义 PCAP／策略让服务器执行，也不能替代本地稳定回归。
5. **分层验收。** 保留现有自制 UAS 的精确断言；加入 pjsua 或 Baresip 的真实栈互通；获准公网测试后再使用公共 URI。若需验证局域网路径，可以把同一个轻量对端放到另一台电脑／Linux 小机，届时单独配置绑定地址和 SIP／RTP 端口。

任何对端都继续遵循录音证据含义：`rx.wav` 是实际收到的解码音频；`tx_source.wav` 是本地发送源重建，不能证明远端收到。验证发送链路应使用回声、对端日志／录音或对端 PCAP。

## 4. 核查记录

- 公开 HTTP 原文、SHA256、获取时间及失败项：本机 `research/sip-call-automation-2026-09-19/test-endpoint-sources/manifest-*.json`。
- DNS 原始观察：同目录 `dns-observation.json`。只做标准 DNS 查询，没有扫描 SIP 端口或发送 SIP 请求。DNS 是当时的服务发布信息，不是端口可达性或通话成功证明。
- 本地 CLI 帮助、默认 UAS XML、源码摘要：同目录 `local-evidence/`。代码核查使用现有 PJSIP 2.17／SIPp v3.7.7；Baresip 固定提交 `f48c14bc35b55eb4443c627ef77f578a91e2a9ff`，Diago 固定提交 `70027f901552802f0f6b9f741d5a33542493963c`。
- 原实现和通话验收范围仍以 [sip-validation.md](sip-validation.md) 为准；本篇新增的是调研结论，没有新增任何成功通话声明。
