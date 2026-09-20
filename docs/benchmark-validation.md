# 0.13.1 中文时序验证记录

日期：2026-09-20。基线 `origin/main=7e9e30d`，开发分支 `feat/v0.13.1-voice-benchmark`。PR 前再次 fetch，主线未变。参考仓库独立克隆到相邻的 `telephony-voice-agent-benchmark`，提交 `fdc4410316110a163c25efecb53aa66459526b7c`；远端为 `https://github.com/ictinnovations/telephony-voice-agent-benchmark.git`，工作区保持干净。

## 验证结果

| 层级 | 实际执行与结果 | 证据范围 |
|---|---|---|
| Python 全量回归 | `python -m unittest discover -s tests -v`：425 项，381 通过、44 按条件跳过 | Python 3.9；跳过的原生测试不计通过 |
| 最终离线专项 | `python -m unittest tests.benchmark.test_analysis tests.benchmark.test_delivery -q`：29 项通过 | 含全量回归后新增的低音量、缺失音频、禁原生模块导入三项测试 |
| macOS 原生时序 | 显式设置 `VOICE_TOOLS_SIP_LOOPBACK=1`，8 项通过 | PJSIP 2.17，真实 127.0.0.1 UDP SIP/RTP，合成音频 |
| 旧 SIP 原生用例 | 同一原生回归中的 19 项旧用例通过 | 收发、认证、早期媒体、DTMF、无 RTP、挂断、re-INVITE、停止等；没有外部线路 |
| Linux 安装版 | 禁外网 arm64 容器，34 项专项通过（含 8 项原生回环） | 使用镜像内安装的 0.13.1，不用源码目录覆盖安装；在新增三项纯测试之前执行 |
| Studio 回归 | Node 24 项通过；CLI 兼容 9 个用例通过 | 16 次正常 CLI 操作和 1 次预期拒绝 |
| 任务迁移 | macOS 单次及批次、Linux 批次执行完成，均生成结果包与复查页 | 打包后删除原始素材目录，依靠包内素材；批次迁移为单项队列，用于验证批次链路，不是并发容量测试 |
| 实际浏览器 | 1440×1000 桌面、390×844 移动端编排与复查完成 | 实际下载场景／任务／三次重复队列，CLI 校验并执行；资源加载正常，控制台 0 错误 |

初轮完整回归发现旧原生测试替身没有新增的 `AudioMediaPort` 边界，补齐替身后重新通过。macOS 原生合并测试的一次“忽略插话”用例返回非零，初轮断言未保存详细原因；最终 8 项时序回环全部通过，Linux 对应项也通过。该偶发退出没有被包装成已定位根因或长期稳定性证明；测试失败断言现会展示执行回执，便于后续追踪。

## PR #25 评审修正验证

2026-09-20 在同一功能分支修复四条评审意见，保留未发布版本 0.13.1。再次同步主线，`origin/main` 仍为 `7e9e30d`。

- `benchmark analyze/summarize` 的超限回执在任务中记为 `findings`，后续依赖步骤可以继续；进程退出 1 但缺少有效回执时仍为失败。使用实际打包与子进程执行验证两种命令，没有模拟任务状态。
- 数值配置先做类型和有限区间比较，超大 JSON 整数、NaN／无穷和非法类型返回稳定输入错误。超大媒体帧时间归为证据不足。
- 批次汇总校验回执类型、版本、状态、非空任务数组、唯一 ID 和输出引用；空对象／损坏结构返回 JSON `INVALID_INPUT`、退出 2。取消、失败及缺少音频证据的任务仍计入分母。
- 运行时与快照 schema 同时声明 SIP 1.0／1.1 可读版本，并区分普通模板写出 1.0、中文时序模板写出 1.1；保留旧字段兼容。

Python 3.9 相关回归 **94 项全部通过、无跳过**，包含新增的 9 项评审回归：

```sh
python -m unittest tests.benchmark.test_analysis tests.benchmark.test_delivery tests.benchmark.test_review_regressions tests.task.test_delivery tests.test_machine_cli tests.sip.test_offline tests.sip.test_runner tests.sip.test_batch_load -v
```

更新本地 `voice-tools-executor:0.13.1` 后，在禁网 Linux arm64 容器中使用安装包执行 `tests.benchmark.test_review_regressions`，**9 项全部通过**。只挂载测试与 schema 快照，没有用源码覆盖安装包。镜像 ID：`sha256:78ebd48b0982f196ed9a25eddb4d63ff5238a2d311153549b4e9680588e5217a`。

使用 `code-readability` Skill 复查相对最新主线的差异及任务状态、批次输出、机器封套调用点；校验入口与汇总职责分开，补上崩溃退出码与有效超限回执的区别，未发现本轮范围内待处理的可读性问题。`git diff --check` 通过。

日志保存在本机独立验收目录（以下以 `<local-audit-dir>/` 代称）：`pr25-review-related-tests.log`、`pr25-review-docker-build.log`、`pr25-review-linux-tests.log`。本轮没有修改页面或原生媒体链路，未重复浏览器／原生回环验收，也未新增真实中文电话验收。

## 明确覆盖的反例

- 持续收取静音 PCM 的 800ms 间隔；时长根据实际样本数计算，包括 40ms 消息。
- 插话后当前声音停下，再出现新回答：后者不延长前者的停声耗时。
- 用户起声之前已安静：插话场景无效，不能输出“0ms 成功”。
- 未确认停声：保留声学观察下界；尾部不足 200ms 的静音不延长下界。
- 主叫挂断／资源清理后的静音，不算被测端响应插话。
- 无首声、低音量、丢帧、队列溢出、缺失／截断 WAV、帧次序与时钟间断、媒体代次改变。
- 人工源音频标注按真实源样本映射；缺少用户语音时不能跳过已配置的响应断言。
- 写入异常保留 incomplete 清单并关闭资源；原有中断错误不被时序断言覆盖。
- 旧 1.0 场景／队列、无 PJSUA2／WebRTC 导入的 schema／模板／校验／预演，禁止联网预检及环境覆盖。

原生回环覆盖双向收发、等待声音、800ms 间隔、响应／忽略插话、提前挂断、等待超时、SIGINT 停止及 re-INVITE 媒体重建。媒体重建后保留录音，但精确指标标为证据不足。

## 任务与浏览器证据

可复现单次和批次验收：

```sh
python scripts/benchmark_validate.py --out outputs/benchmark-call-validation
python scripts/benchmark_validate.py --out outputs/benchmark-batch-validation --batch
# 使用实际浏览器下载文件，文件名为 interrupt.json、task.json、queue.json
python scripts/benchmark_validate.py --out outputs/benchmark-export-validation --browser-downloads /path/to/downloads
```

脚本只生成合成音，启动本机独立测试端，检查场景与包，再删除源目录、执行、收集和生成复查。需要现有 PJSUA2 运行环境。参数覆盖由执行机 `lab.sip` 提供；包内不携带凭据。

本次保留的运行示例：

- `outputs/benchmark-mobile-export-final/`：移动端导出实际文件；validate、call、timing 三步完成。声学停声测量 99.42ms，新声音延迟 559.27ms，仅为该合成回环观测。
- `outputs/benchmark-batch-final/`：macOS 的 sip batch → summarize → collect → review。
- `outputs/benchmark-linux-final/migration/`：Linux 安装版批次迁移、结果包与离线复查；汇总显示有效 1、失败／无效／证据不足各 0，各指标 n=1，明确不解释为统计稳定的性能分位数。
- `outputs/benchmark-review-final/`：双向时间轴、首声／插话／停声／再次出声和录音定位。点击运行时钟 1.343s 的停声标记，实际播放器 `currentTime=1.280123s`，与拼接 RX 的 1.280s 位置一致；音频响应 HTTP 206。

页面没有浏览器拨号或上传录音功能。普通 Python 静态服务器不支持 Range，初次试听检查发现不能按时间跳转；改用仅监听 127.0.0.1 的 Range 静态服务器后通过，并在使用说明记录该部署要求。工具禁止 `file://` 导航，故本次浏览器验收通过 HTTP 完成，不宣称实际验证了双击 HTML 打开。

日志与截图保存在上述本机验收目录，包括 `full-tests-final.log`、`benchmark-final-offline.log`、`native-final.log`、`native-timing-final.log`、`linux-installed-tests.log`、`mobile-export-validation.log`、`linux-migration-final.log`、`desktop-editor.png`、`mobile-review.png`。这些可重建的合成录音与长日志未提交仓库。

## Docker 与版本

本地镜像 `voice-tools-executor:0.13.1` 复用已存在的 0.12.1 arm64 原生运行层，在禁网构建中通过 `pip install --no-deps --no-build-isolation .` 安装本次源码并执行 `pip check`。本轮没有重新编译 PJSIP／ViSQOL，也没有新增音质评分验收。标准部署仍使用仓库 `docker/task/Dockerfile`／`vt build`；离线目标机须预先具备镜像。

Python 包、默认执行镜像标签、运行时 CLI schema 和指南统一为 0.13.1，基础源码保持 Python 3.9 兼容。容器为 Linux arm64；amd64 构建和跨物理执行机的网络互通本次未验证。

## 可读性核查

使用 `code-readability` Skill 核对相对最新 `origin/main` 的新增 benchmark 模块、SIP 适配器／runner／场景、任务预检／复查、独立 `benchmark.js` 及必要调用点。按命名、控制流、职责、复用和注释边界检查：

- 观测回调与文件／检测工作分开；源播放位置与保存 PCM 位置有明确职责，未用调度轨冒充出站媒体。
- 保留独立配置、纯时序规则、证据读取、模板和汇总职责；单独提取挂断范围裁剪，避免将清理静音混入指标。
- 页面时序模块独立于已有任务状态机，已有 `app.js` 只增加入口与音频定位钩子；未扩大为整仓格式化。
- 修复关闭失败仍标完整、缺失测量误跳过、空汇总误算有效、挂断静音混入停声及错误导出入口等功能问题，并完成对应回归；同时检查新页面动态文本转义。
- 保留既有 SIP 1.0 返回与执行顺序、任务包校验机制和断言失败协议。未发现本次范围内尚需阻塞交付的可读性问题。

## 未验收项

**尚未提供获授权中文真人录音、指定 FreeSWITCH 测试账号／线路及业务预期。** 因此本版交付的是软件、模板、离线算法、原生合成回环与任务迁移闭环，不标记真实中文电话场景已验收。口音、噪声、低音量中文素材的 VAD 校准、误打断的人判断和长期并发稳定性须另行实测。
