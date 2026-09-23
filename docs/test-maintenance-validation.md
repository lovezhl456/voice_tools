# 测试维护验证

## 0.18.2 测试套件精简

日期：2026-09-23。基线为已同步的 `origin/main` 提交 `1a761ec0d6de7085f2247d9bd531db2755b53c45`，分支 `test/v0.18.2-suite-streamline`。本轮只修改测试和维护文档；产品 `src/`、包版本、CLI 参数和页面资源未变。

### 保留范围与覆盖迁移

| 既有检查 | 本轮处理 | 保留的保护 |
|---|---|---|
| 本机检测服务 HTTP | 仅把测试服务器 `serve_forever` 轮询间隔设为 0.01 秒 | 每例仍有独立数据库、音频、会话及真实本机 HTTP 请求；来源/会话拒绝、写锁冲突、Range 音频及结果持久化不变 |
| 号码抓包归档校验 | 正常归档按需建立一次，逐例复制至独立目录后修改；生产者故障测试继续重新生成数据 | manifest 数量/类型/内部身份、选择条件、部分成功、损坏事件、子进程清理仍各自验证 |
| 跨文件合成素材 | RTP、号码抓包、gaps、benchmark 和整通 QA 的公共素材移到对应 `fixtures.py` | 内容、采样率、摘要与失败分支未变；测试不再实例化其他 `TestCase` 来准备素材 |
| 共享复核页静态结构 | 把播放控件与资源存在性检查并入已有共享页面测试，删除原来独立的同源方法和少数重复/易变标题断言 | 各业务入口的内嵌数据、真实音频路径、vendor 摘要/许可、转义及脚本顺序继续由原测试验证；桌面/手机浏览器流程未删 |

Python 方法数 652 → 651，减少的是与共享页面同源的一项静态检查。`tests/recording_qa/test_file_filter.py` 现在承接了播放控件 ID 与共享资源断言。测试方法数不是本轮优化目标；20 场景 × 2 采样率及其他拒绝分支仍保留。

### 同负载计时

同一台 macOS、同一 Python 3.9.6 虚拟环境和仓库源码下交替运行旧/新测试，每组各三轮，均无失败/跳过。旧版号码归档测试模块取自上述 `origin/main` 提交；共用业务实现和合成输入相同。结果仅代表当前测试环境，不推断生产抓包性能。

| 固定测试组 | 旧版三轮（秒） | 新版三轮（秒） | 中位耗时变化 |
|---|---|---|---|
| 检测服务 10 项 | 5.783、5.791、5.825 | 0.688、0.639、0.643 | 5.791 → 0.643，约减少 5.15 秒 |
| 号码归档审查 14 项 | 6.881、6.875、6.837 | 4.493、4.513、4.501 | 6.875 → 4.501，约减少 2.37 秒 |

检测服务只改测试服务器的退出轮询；它在运行期间会更频繁唤醒。号码归档的只读基础资料在首次需要时生成，每项复制后再改，避免测试互相污染。两组之外的全量耗时没有固定同负载前后对照，不据历史测试时长计算总体提速。

### 最终验证与边界

从仓库当前 `src/` 导入，临时关闭真实 NISQA/QA 模型、latency 引擎和原生 SIP 回环环境开关，执行 `docs/testing.md` 的三条默认全量命令：

| 检查 | 最终结果 |
|---|---|
| Python 3.9.6 `unittest discover -s tests -v` | 651 项，597 通过、54 条件跳过、0 失败/错误；最终状态 71.549 秒 |
| Studio `node --test tests/studio/core.test.cjs` | 24/24 通过 |
| Studio/CLI `python tests/studio/check_cli_compat.py` | 9/9 场景通过，含 16 次成功 CLI 操作及 1 次预期拒绝 |

54 个条件跳过中，37 个属于未启用的原生回环/真实模型/真实引擎专项；17 个由当前 Python 环境缺少 `soundfile` 导致，后者是普通 NISQA 假评分器和 gaps 评分来源测试的依赖缺口。没有新增跳过条件。`soundfile` 相关产品功能未改；若后续修改对应功能，须安装依赖补验。检测服务和进程清理用例在允许本机端口及 `ps` 查询的环境通过。真实模型、原生通话、真实录音与安装 wheel 的桌面/手机浏览器流程未在本轮重复执行，因为产品运行代码和页面未修改。

本轮选测还覆盖号码抓包与其会话/报告调用方、gaps/benchmark/latency 混合任务、整通 QA 以及共享复核页；发现的两处夹具导入遗漏已修正并在最终全量中通过。`git diff --check` 通过，测试选择映射见 [测试指南](testing.md#按影响选择测试)。

以下保留首轮 0.14.1 整理的原始基线与结果；后续合入 0.15.1 的验证单列在 [主线同步记录](#合入-0151-主线后的验证)，两轮结果不相互替代。

日期：2026-09-21。基线 `d4d073d4961f2086341dbc7c4249cf8341b28450`，分支 `test/v0.14.1-suite-maintenance`。实施前及最终验收前均已 fetch 主线，基线未变。沿用 0.14.1，仅修改测试与维护文档。

## 影响范围

`src/`、产品 HTML/JS、脚本、`pyproject.toml` 和 CLI schema 无差异，公开接口、输出、依赖和包资源未修改。测试侧新增按需录音辅助函数与只读 HTML 解析辅助类；不复制检测器判断、不导入其他 TestCase 类来复用准备、不 mock 核心分析和复核流程。

QA、HOMER、benchmark 产物检查与共享复核链路是本轮选测重点；0.14.1 的 gaps 及任务迁移测试保留并进入默认全量。旧 QA 的 freeze／promote／evaluate／compare，以及 gaps 与 benchmark 混合任务继续验证。页面检查只代表导出产物完整，不是交互验收。

## 覆盖迁移与保留

| 原场景 | 整理后位置及保护 |
|---|---|
| workflow 全量批处理与试听复制 | 原 `test_cli_full_batch_with_portable_audio` 保留 20 个场景、逐条状态、复制音频和复核行数 |
| 生成器复现性 | 原 `test_seed_and_hash_reproducibility` 显式生成两套各 20 份录音，比较 manifest 和摘要 |
| 坏录音隔离 | 原用例改为 1 份有效＋1 份损坏输入，除总数/错误数外断言有效记录及候选状态仍在 |
| 非法事件、HTML 转义 | 原用例使用 missing＋normal 两份录音；保留错误数和转义断言 |
| 覆盖保护、空输入、输入去重、声道覆盖 | 原方法均保留，各用一个相关场景 |
| 人工复核晋升及指标 | missing＋normal 两份录音，保留 CLI promote/evaluate、TP/TN/FP/FN 和 synthetic 标识 |
| 缺少 reviewer、摘要篡改、时区/窗口错误、缺预测、半成品/重复标签、不确定标签、坏 JSONL | 原方法各使用一份 missing 录音，保留各自错误分支；noise 假阴性仍使用 noise |
| 帮助及非法超时 | 不生成录音，检查返回 2、具体 `timeout_s` 错误和输出未创建，防止输入缺失掩盖参数检查 |
| workflow 独立 findings 退出码 | 移入 `MachineCliTests.test_analysis_exit_codes_in_text_and_json` 的文本模式，不再单独重复准备全套素材 |
| 原 JSON 成功/findings/部分失败用例 | 生成器分离为 `test_json_generator_preserves_all_scenarios`，保留 20 场景；分析用例以文本/JSON × 0/1/2/3 八个子场景覆盖，保留错误码、状态和实际产物/有效记录 |
| HOMER 参数前置/后置 | 原方法环境 URL 改为错误路径，显式 URL 为正确路径；实际模拟请求必须命中 `/homer/api/v3/` |
| benchmark 工作台字符串检查 | 原断言保留；增加内嵌 JSON、五种模板、catalog、JS 顺序、CSS 引用及复制资源字节一致检查 |
| QA 工作台和片段映射 | 原断言均保留，包括 beforeunload、播放入口及许可；增加内嵌数据/隐藏路径、单/双轨 WAV 帧数、片段长度和共享 vendor manifest 摘要与完整嵌入检查 |

Python 方法数仍为 482：workflow 18 → 17，统一 CLI 4 → 5。子场景有所增加，未以降低方法数为目标。两种采样率的 20 场景 detector 回归和其他抓包、媒体、证据、超时、权限及覆盖保护测试均未删改。

## 环境与实测

macOS ARM64、Python 3.9.6、NumPy 2.0.2、Node 24.18.0；源码来源已确认是当前独立工作树。tcpdump、tshark、mergecap、FFmpeg/ffprobe、dumpcap 与 WebRTC VAD 可用；当前 Python 无 soundfile。所有普通运行临时取消模型与 SIP 回环开关，不访问真实 HOMER 或外部线路。

| 检查 | 本轮结果 |
|---|---|
| 修改前选测 | 146 项，145 通过、1 项 soundfile 条件跳过；30.74 秒 |
| 修改后同组选测 | 146 项，145 通过、同一项条件跳过；26.68 秒 |
| 自检补强的非法超时单项 | 通过，错误来源明确为 timeout_s |
| 最终 Python 默认全量 | 482 项，437 通过、45 条件跳过，0 失败／错误；68.572 秒 |
| Node 与 Studio/CLI 兼容 | Node 24 项全部通过；兼容脚本 9 场景通过（16 次正常 CLI 操作＋1 次预期拒绝） |
| 文档与范围检查 | 117 处本地链接／锚点、11 个新增或修改的 Bash 块语法、8 组测试选择收集、CI 示例 YAML 解析及 diff 检查通过 |

选测范围：QA 的 detector/workflow/P0、统一 CLI、HOMER client/entrypoints、benchmark delivery/review_regressions、gaps delivery/evidence、task delivery。基线及修改后唯一跳过为 `test_nisqa_provenance_keeps_scoring_rows_unchanged`，原因均为 soundfile 缺失；本轮未改变其评分来源逻辑，不把该项写成通过。

最终全量的 45 项跳过分为：19 项 SIP 原生回环、8 项 benchmark 原生回环、1 项真实 NISQA 模型（均未启用）；16 项 NISQA 服务与 1 项 gaps 评分来源兼容（缺少 soundfile）。后两组不是重型模型测试，本轮未改其逻辑，明确保留未执行边界。未新增或修改跳过条件。HOMER 在允许本机端口绑定的环境运行，本次无需补跑。

QA workflow 的输入录音通过任务本地统计钩子计数：**380 → 76 份（减少 80%）**。完整批处理仍生成 20 份，复现性仍生成 40 份，其余合计 16 份。帮助/非法参数生成 0 份。workflow 在相同统计方式下约 **6.09 → 1.54 秒**；包括子进程等待，统计钩子会有额外开销，只是本机单次观察，不是标准 unittest 耗时或稳定性能承诺。统计程序和原始日志不作为项目运行器提交。

## 实际命令与证据

基线及修改后选测使用相同解释器、环境和模块；任务本地测量在标准 unittest loader/runner 上记录计时和输入写入次数。下列原生命令可复现相同选测范围，不含统计钩子：

```bash
env -u VOICE_TOOLS_SIP_LOOPBACK -u VOICE_TOOLS_NISQA_MODEL_DIR -u VOICE_TOOLS_NISQA_AUDIO \
  PYTHONPATH="$PWD/src" python -m unittest \
  tests.recording_qa.test_detector tests.recording_qa.test_workflow tests.recording_qa.test_p0 \
  tests.test_machine_cli tests.homer.test_client tests.homer.test_entrypoints \
  tests.benchmark.test_delivery tests.benchmark.test_review_regressions \
  tests.gaps.test_delivery tests.gaps.test_evidence tests.task.test_delivery -v
```

首轮最终默认全量直接使用当时 [测试指南](testing.md#默认全量三项均需记录) 的 Python、Node、CLI 兼容三条命令；修改后仅一项断言继续补强，先单项复验，再进入最终全量，不机械重跑整个选测组。仅文档收尾不重复全量。

普通命令的环境隔离已用预设的三个测试开关验证。文档中的重型专项只检查 Bash 语法、选择的模块及收集数量，没有执行真实模型或原生通话；不将命令检查写成专项通过。

本地原始证据：`baseline.log/json`、`related.log/json`、`review-fix.log`、`full.log`、`node.log`、`compat.log`、`doc-checks.json`。这些文件保留于本任务独立证据目录，不提交临时录音、环境或长日志。

## 自检、可读性与边界

按 `code-readability` Skill 检查相对主线的测试差异及必要调用点。辅助函数只承担写指定样本和读取 HTML 的职责；CLI 子场景有独立输出目录和明确失败标签；保留旧页面断言，补充独立业务字段与实际文件检查，没有引入配置框架。

实施自检第一轮发现：不再生成录音后，非法参数测试需要明确错误来源，避免将不存在输入误当作超时校验通过。补充 `timeout_s` 与输出未创建断言，单项复验通过。第二轮复核覆盖迁移、资源路径、依赖分层及生产文件差异，未发现新的明显问题，提前结束。此前计划已完成三轮审查；后续计划自检规则已写入仓库约定。

本轮未运行真实模型、PJSUA2 回环、真实 ViSQOL 后端、生产服务、Linux/其他 Python 版本或浏览器交互。未修改产品页面或打包内容，因此不重复页面验收、构建 wheel 或镜像。CI 示例仍未启用；其语法和说明检查不能当作 GitHub Actions 实际通过。

## 合入 0.15.1 主线后的验证

日期：2026-09-21。按用户要求，将最新主线 `73c383e` 合入原维护分支并更新 PR #29，产品版本随主线为 0.15.1。主线新增 latency 功能与文档脱敏；唯一文本冲突为 CHANGELOG，保留双方迭代记录。相对 `73c383e`，本 PR 仍只修改测试和维护文档；产品源码、HTML/JS、接口、打包配置、依赖和主线新用例均无改动。

合并自检第一轮发现并处理两个适配点：共享任务页新增 `latency.js`／`latency.css`，原资源列表断言需同步，并继续逐个核对资源的实际来源与字节；普通测试需临时清除新增的 `VOICE_TOOLS_TEST_LATENCY_DIR`，防止继承环境时意外执行真实引擎。统一指南补充 latency 的依赖、选测关系和独立引擎命令，同步相关维护入口；保留主线历史专项记录。第二轮复查范围、调用点、环境开关、文档和交付要求，未发现新的明显遗漏，提前结束。

环境沿用首轮的 macOS ARM64、Python 3.9.6、NumPy 2.0.2 和 Node 24.18.0，没有安装依赖。重新确认导入当前工作树的 `voice_tools`，版本为 0.15.1；普通命令中的四个开关均已通过预设环境值验证临时清除。

| 检查 | 合并后结果 |
|---|---|
| 相关选测 | 54 项：首次 53 通过，1 项因沙箱禁止 `ps` 未完成；仅补跑该项后通过，去重后 54 项通过 |
| 最终 Python 默认全量 | 501 项，449 通过、52 条件跳过，0 失败／错误；65.544 秒 |
| Node 与 Studio/CLI 兼容 | Node 24 项通过；CLI 兼容 9 场景通过（16 次正常操作＋1 次预期拒绝） |
| latency 普通检查 | 契约与页面用例进入 Python 全量，页面用例内的现有 Node 渲染检查含 16 个子场景；没有另重复执行渲染组 |
| 文档与范围 | 128 处本地链接／锚点、13 个新增或修改的 Bash 块语法、10 组测试选择收集、CI 示例 YAML 和差异检查通过；相对新主线仍为原 16 个测试／文档文件 |

52 项跳过：19 项 SIP 原生回环、8 项 benchmark 原生回环、1 项真实 NISQA 模型、7 项 latency 真实引擎，均未启用；另有 16 项 NISQA 服务和 1 项 gaps 评分来源兼容因缺少 soundfile 未执行。未增加或修改跳过条件，后 17 项仍属于普通测试且不记为通过。合并后的默认全量在允许本机 HTTP 端口与测试进程状态查询的环境完成，不连接真实服务。

本轮选测命令为：

```bash
env -u VOICE_TOOLS_SIP_LOOPBACK -u VOICE_TOOLS_NISQA_MODEL_DIR -u VOICE_TOOLS_NISQA_AUDIO -u VOICE_TOOLS_TEST_LATENCY_DIR \
  PYTHONPATH="$PWD/src" python -m unittest \
  tests.benchmark.test_delivery tests.benchmark.test_review_regressions \
  tests.task.test_delivery tests.gaps.test_delivery tests.latency.test_contract \
  tests.latency.test_web tests.test_machine_cli -v
```

补跑仅选择 `tests.latency.test_contract.ContractTests.test_timeout_kills_engine_and_descendant`，沿用同一解释器、源码和四个开关的清除方式。最终全量使用当前 [测试指南](testing.md#默认全量三项均需记录) 的三条命令；提交 Git、更新 PR 说明和说明文档收尾后复用该结果。

按 code-readability Skill 复核相对新主线的最终测试差异及共享任务页生成调用点。资源断言只依据文件归属选择两个已有资源目录，保留显式的顺序与完整性检查，没有增加配置层或修改产品逻辑。QA 素材优化未再改动，380 → 76 的计量保留为首轮数据，不重新声称是 0.15.1 的性能对照。

原始日志另存为 `merge-related.log`、`merge-related-retry.log`、`merge-full.log`、`merge-node.log`、`merge-compat.log`、`merge-env-isolation.log` 和 `merge-doc-checks.json`。本轮没有运行 latency 真实引擎、真实模型、PJSUA2 回环、真实 ViSQOL 后端、生产服务、跨平台或浏览器交互验收；这些不以主线已有记录冒充本轮通过。
