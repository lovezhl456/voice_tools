# 测试选择与运行

小改动先运行受影响模块及其调用方；公共契约、跨工具流程变化和含代码／测试变更的 PR 交付前执行默认全量。规则见 [仓库约定](../AGENTS.md)，本次整理证据见 [测试维护验证](test-maintenance-validation.md)。

## 环境与源码来源

所有命令从当前工作树根目录执行，`python` 指向选定的测试虚拟环境。可以复用已有依赖环境，但不能让 editable 安装将测试导向旧工作树。先检查：

```bash
PYTHONPATH="$PWD/src" python -c 'import sys, voice_tools; from pathlib import Path; source = Path(voice_tools.__file__).resolve(); print(sys.executable); print(source); assert source.is_relative_to(Path.cwd().resolve() / "src")'
```

下面的 `PYTHONPATH` 和 `env -u` 只作用于当前命令，不改变 shell 配置或模型路径设置。即使已经设置真实模型／latency 引擎／回环环境变量，普通命令也不会启用它们。

| 依赖 | 需要它的检查 | 缺失时如何处理 |
|---|---|---|
| Python 3.9+、项目基础依赖 NumPy | Python 测试 | 必需；使用已有环境或按项目安装说明准备 |
| Node.js 18+（支持 `node --test`） | Studio、CLI 兼容，以及 Python 内的浏览器导出和 latency 渲染用例 | 普通全量必需，不能将命令不存在算跳过成功 |
| tcpdump、tshark、mergecap | 离线过滤、抓包分析、跨文件重组 | 普通全量必需；部分测试没有条件跳过，缺失会失败 |
| 本机回环 HTTP 端口权限 | HOMER 模拟服务及会话关联 | 环境受限时在允许本机端口的环境补跑受阻组，无需真实 HOMER |
| `ps` 及查询测试子进程状态的权限 | latency 超时清理契约 | 若沙箱禁止执行 `ps`，在允许查询的环境只补跑受阻用例，保留清理断言 |
| FFmpeg／ffprobe、dumpcap | 可选格式转换、FIFO 轮转集成 | 缺失按原测试条件跳过并记录；改动涉及这些行为时必须补验 |
| soundfile（版本范围沿用项目可选依赖） | NISQA 假评分器服务、gaps 评分来源兼容 | 不需 Torch 或模型；缺失会跳过。相关功能受影响时需补齐，不能视为已验证 |
| webrtcvad | 可选 VAD | 缺失按原条件跳过，受影响时补验 |
| pip（当前 Python 环境） | latency 依赖摘要拒绝测试 | 普通全量必需；仅检查本地伪造 wheel，不联网安装依赖 |
| 独立安装的 latency 引擎、其 Python 3.11／3.12 与锁定依赖 | latency 真实引擎专项 | 普通契约与渲染测试不需要；仅显式设置 `VOICE_TOOLS_TEST_LATENCY_DIR` 后运行引擎专项 |
| PJSUA2；NISQA 完整运行库、权重及音频；ViSQOL 后端 | 原生／真实模型专项 | 与普通回归分开，按对应手册准备；NISQA 真实模型需 Python 3.10+ |

只在需要对应验证时准备依赖，不为文档修改下载模型、编译原生库或构建镜像。缺少必需工具时先解决环境，不修改测试跳过条件。

## 按影响选择测试

使用 `python -m unittest` 的模块或方法选择，不维护固定的“快速测试白名单”。先读调用关系，范围不清时检查直接调用方；存在公共影响时进入默认全量。

| 改动位置或行为 | 局部验证至少覆盖 |
|---|---|
| QA 分析／复核 | `tests.recording_qa.test_detector`、`test_workflow`、`test_p0`；涉及入口加 `tests.test_machine_cli` |
| QA 事件／freeze 或共享复核资源 | 上述 QA 组，并加 `tests.gaps.test_delivery`；涉及旁证加 `tests.gaps.test_evidence` |
| 任务页／结果包复查 | `tests.task.test_delivery`、`tests.benchmark.test_delivery`、`tests.benchmark.test_review_regressions`、`tests.gaps.test_delivery`、`tests.latency.test_web`，含 gaps 与 benchmark 混合任务；涉及 latency 执行／迁移时另跑真实引擎专项 |
| latency 参数／契约／共享渲染 | `tests.latency.test_contract`、`tests.latency.test_web`、`tests.task.test_delivery`、`tests.benchmark.test_delivery`；算法、引擎接入或依赖变化另跑 `tests.latency.test_engine`，含 gaps／benchmark／latency 混合任务 |
| HOMER 参数或分发 | `tests.homer.test_client`、`tests.homer.test_entrypoints`；关联链路加 `tests.sessions.test_homer_link` |
| NISQA 服务／评分来源 | `tests.nisqa.test_service`、`tests.nisqa.test_cli`、`tests.gaps.test_evidence`；真实推理接入变化另跑模型专项 |
| 公共 CLI／封套／格式 | 先跑直接受影响工具和统一 CLI，再默认全量；不能只检查某一个工具 |
| SIP／抓包／会话／报告 | 对应测试目录及下游调用方；公共媒体、证据或导出合同变化进入默认全量 |
| Studio 前端 | Node、CLI 兼容、`tests.sip.test_batch_load`；交互变化另做实际浏览器验收 |

例如，只调整 QA 测试准备与退出码：

```bash
env -u VOICE_TOOLS_SIP_LOOPBACK -u VOICE_TOOLS_NISQA_MODEL_DIR -u VOICE_TOOLS_NISQA_AUDIO -u VOICE_TOOLS_TEST_LATENCY_DIR -u VOICE_TOOLS_TEST_QA_MODEL_DIR \
  PYTHONPATH="$PWD/src" python -m unittest \
  tests.recording_qa.test_detector tests.recording_qa.test_workflow \
  tests.recording_qa.test_p0 tests.test_machine_cli -v
```

只在选择依据需要时扩大范围。合成测试检查工程规则，不证明真实业务通话或人工听感准确率。

## 默认全量：三项均需记录

```bash
env -u VOICE_TOOLS_SIP_LOOPBACK -u VOICE_TOOLS_NISQA_MODEL_DIR -u VOICE_TOOLS_NISQA_AUDIO -u VOICE_TOOLS_TEST_LATENCY_DIR -u VOICE_TOOLS_TEST_QA_MODEL_DIR \
  PYTHONPATH="$PWD/src" python -m unittest discover -s tests -v
node --test tests/studio/core.test.cjs
env -u VOICE_TOOLS_SIP_LOOPBACK -u VOICE_TOOLS_NISQA_MODEL_DIR -u VOICE_TOOLS_NISQA_AUDIO -u VOICE_TOOLS_TEST_LATENCY_DIR -u VOICE_TOOLS_TEST_QA_MODEL_DIR \
  PYTHONPATH="$PWD/src" python tests/studio/check_cli_compat.py
```

这三个命令不启用真实模型、latency 真实引擎或 SIP 原生回环，但会运行本机模拟 HTTP 服务、离线系统工具及受控子进程。Python discovery 会通过 `tests.latency.test_web` 调用现有 Node 渲染测试，不需在全量流程中再单独运行一次。Python 通过不能替代 Studio Node 或 CLI 兼容结果。测试数随版本变化，不能用旧版本数量作通过门槛。

## 独立专项

SIP 与 benchmark 共用原生回环开关，运行时一并选择，所有呼叫限制在本机：

```bash
env -u VOICE_TOOLS_NISQA_MODEL_DIR -u VOICE_TOOLS_NISQA_AUDIO -u VOICE_TOOLS_TEST_LATENCY_DIR -u VOICE_TOOLS_TEST_QA_MODEL_DIR \
  VOICE_TOOLS_SIP_LOOPBACK=1 PYTHONPATH="$PWD/src" python -m unittest \
  tests.sip.test_loopback tests.benchmark.test_native -v
```

前提是当前解释器可导入 PJSUA2；未准备好而被全部跳过不算专项通过。见 [SIP 手册](sip.md#7-本地回环验收与维护) 和 [时序验证边界](benchmark-validation.md)。

NISQA 真实权重使用显式的本地模型和语音，测试禁止下载：

```bash
env -u VOICE_TOOLS_SIP_LOOPBACK -u VOICE_TOOLS_TEST_LATENCY_DIR -u VOICE_TOOLS_TEST_QA_MODEL_DIR \
  VOICE_TOOLS_NISQA_MODEL_DIR="$PWD/.local/nisqa-model" \
  VOICE_TOOLS_NISQA_AUDIO="$PWD/data/speech.wav" \
  PYTHONPATH="$PWD/src" python -m unittest tests.nisqa.test_real_model -v
```

输入路径为操作者自行准备的素材，不是仓库自带数据。安装和评分说明见 [NISQA](nisqa.md)；ViSQOL 的真实后端验证见 [本机验收](visqol-local-validation.md) 和 [Linux 验收](visqol-linux-validation.md)。`tests.visqol.test_cli` 使用假后端，只检查协议和失败处理，不代替真实评分。

latency 真实引擎使用按 [安装说明](latency-install.md) 准备的隔离目录；不在普通全量中自动安装或启用：

```bash
env -u VOICE_TOOLS_SIP_LOOPBACK -u VOICE_TOOLS_NISQA_MODEL_DIR -u VOICE_TOOLS_NISQA_AUDIO \
  VOICE_TOOLS_TEST_LATENCY_DIR="/absolute/path/to/engine" \
  PYTHONPATH="$PWD/src" python -m unittest tests.latency.test_engine -v
```

普通的 `tests.latency.test_contract` 和 `tests.latency.test_web` 不依赖该目录。`VOICE_TOOLS_LATENCY_DIR` 是产品运行配置，`VOICE_TOOLS_TEST_LATENCY_DIR` 才是测试启用开关；使用手册中的显式目录，避免把默认配置误当专项已执行。真实引擎专项含任务迁移与 gaps／benchmark／latency 混合复查，既有验收见 [latency 验证](latency-validation.md)。

页面／交互变化按 [Studio 浏览器要求](sip-studio/README.md#文件与检查)、[复核页既有验收](output-gaps-validation.md#页面验收) 和 [latency 验证](latency-validation.md) 检查桌面与移动端、播放、定位、导入导出和控制台。页面字符串或资源检查仅证明产物完整性。

## 结果有效性与补跑

- 记录完整提交或基线＋工作区差异、源码路径、解释器／关键工具版本、选测理由、命令、耗时和通过／失败／跳过。原始日志放在忽略的本地产物目录，提交文档仅保留汇总及复现命令。
- 公共改动与交付前的全量可以是同一次。同一代码、测试、依赖及执行条件未变时，不因 Git 提交、PR 文案或审查再全跑；仅说明文档变化不使结果失效。实质环境变化或新失败证据需要重新判断范围。
- 后续代码变化先选测；交付前最终代码状态需有默认全量结果。为准备基线不机械加跑第二遍全量；可先测相关组，遇到无法归因的失败再在未改基线上复现对应用例。
- 计划中的重型专项未启用与缺失普通测试依赖分别记录。新增跳过、受影响必要用例未执行、未处理失败均不能写成完整通过。
- 若仅 HOMER 的 `setUpClass` 因本机端口报 `PermissionError`，记录未进入的方法，在允许本机绑定的环境补跑下列两组；其他失败另行处理，不笼统归因给沙箱：

```bash
env -u VOICE_TOOLS_SIP_LOOPBACK -u VOICE_TOOLS_NISQA_MODEL_DIR -u VOICE_TOOLS_NISQA_AUDIO -u VOICE_TOOLS_TEST_LATENCY_DIR -u VOICE_TOOLS_TEST_QA_MODEL_DIR \
  PYTHONPATH="$PWD/src" python -m unittest \
  tests.homer.test_client.CliIntegration tests.sessions.test_homer_link.HomerLinkTests -v
```

合并结果时按用例去重，注明哪些组来自补跑。旧验收文档只证明其日期和基线下的结果，不替代当前验证。

## 安装包浏览器验收

复核流程的能力保留与拒绝条件见 [R01–R08 基线](review-acceptance.md)。从仓库根目录，在隔离的开发环境准备 Python 3.10+、Node.js 20+，然后安装开发依赖：

```bash
python -m venv .venv-review
. .venv-review/bin/activate
python -m pip install 'numpy>=1.24,<3' 'setuptools>=61' wheel
npm ci --prefix tests/browser --ignore-scripts --no-audit --no-fund
tests/browser/node_modules/.bin/playwright install chromium
python scripts/check_review.py
```

Linux 缺少浏览器系统库时，将安装浏览器的命令改为 `playwright install --with-deps chromium`（使用上面的本地可执行文件路径）；系统包安装可能需要管理员权限。Playwright 版本由 `tests/browser/package-lock.json` 固定，仅用于开发验收，不加入产品运行依赖。

运行器构建并安装 wheel，确认夹具从安装目录导入产品，生成合成音频和确定性模型证据，启动临时本机服务，再执行桌面和手机两组 Chromium 流程。整个过程不依赖个人录音、本机固定端口、真实模型或外部页面。退出时关闭服务。浏览器视口模拟不代表真实手机设备或其他浏览器已验证；模拟模型也不代表实际准确率。

默认产物位于新的 `.artifacts/review-时间-随机后缀/`；可用 `--out .artifacts/review-check` 指定**新的或空的目录**，避免覆盖历史证据。关键文件：

- `verification.json`：提交、工作区状态、wheel SHA-256、版本、命令、能力覆盖及最终状态。
- `browser-results.json` 和 `browser-report/index.html`：逐项结果与可查看的报告。
- `browser-results/`：实际页面截图、导出 CSV，失败时的截图和 trace。
- `build.log`、`install.log`、`fixtures.log`、`browser.log`：分阶段日志；复现用 wheel 和夹具保存在 `wheels/`、`site/`。

需要定位问题时，可以选测；既有 wheel 的产品内容未变时可以复用，避免再次构建：

```bash
python scripts/check_review.py --project desktop --grep R03
python scripts/check_review.py --wheel .artifacts/previous-run/wheels/voice_tools-0.16.2-py3-none-any.whl
tests/browser/node_modules/.bin/playwright show-report .artifacts/review-check/browser-report
```

第一条仅验证所选项，记录为 `coverage=selected_cases`。完整验收必须不带 `--project`／`--grep`，得到 `status=passed`、`coverage=full_baseline`，且 desktop/mobile 各包含 R01–R08；失败、跳过、空测试、遗漏能力、仅重试后成功都不算通过。使用 `--wheel` 时应确认其包含当前产品改动，并以记录的摘要为准。

[Review acceptance 配置示例](examples/review-acceptance.yml)目前尚未启用：推送凭据缺少 `workflow` 权限。获得明确授权后，可将该文件部署为 `.github/workflows/review-acceptance.yml`，在每个 PR、main 更新和手动触发时运行关键规则回归及相同的安装包浏览器检查，并上传有效期为 7 天的日志、报告、截图和 wheel。部署后仍须观察一次真实 CI 运行；配置文件本身不证明检查已通过。CI 不替代 Python／Node／CLI 默认全量，也不自动改变 GitHub 分支保护。

## 整通自动质检

普通规则、模型合同及工作流验证无需权重：`tests.recording_qa.test_assessment`、`test_assessment_workflow`、`test_speech_model`、`test_assessment_delivery`。波形摘要、离线资源和旧结果兼容用 `test_assessment_waveform`；共享波形壳变化另跑原 QA `test_p0`、筛选与 gaps 交付组，并在实际浏览器验证缩放、拖动、区间播放和手机布局。安装器契约用 `test_setup`，以假子进程覆盖选择、JSON、当前解释器、部分失败、重试和任务排除，不在普通测试中执行 pip 下载。变更影响共享证据加载、任务执行或复查时加原 QA、task、gaps 交付组；页面变更另做桌面／移动端实浏览器验证。

安装器或依赖变化时，另建一次性虚拟环境，从构建 wheel 安装基础 CLI；在未装 ONNX Runtime／SciPy 的环境实际执行 `qa setup --component all --model-dir 临时目录`，核对 JSON、缓存重跑、交互选择和 doctor 推理。禁止在测试中改用户现有全局环境。离线 wheelhouse 准备和失败排查见[安装说明](automatic-qa-install.md)。安装成功不等于真实通话准确率通过。

真实 CPU 模型专项必须显式提供本机权重和语音，不在测试中下载：

```bash
VOICE_TOOLS_TEST_QA_MODEL_DIR=/absolute/path/to/autoqa \
VOICE_TOOLS_TEST_QA_AUDIO=/absolute/path/to/speech.wav \
PYTHONPATH="$PWD/src" python -m unittest tests.recording_qa.test_assessment_real_model -v
```

该专项检查真实推理、状态隔离和重采样；不能替代真实业务录音上自动通过覆盖率和漏检率的评估。

## 自定义检测与标签库

选测：`python -m unittest discover -s tests/detect -t . -v`，覆盖组合配置、信号预期值、窗口／排除、跨批次检索、不可变版本、事务复核、历史与误报／漏报比较、CLI 和报告来源摘要。公共命令注册改变后执行默认全量。

页面复用共享波形／播放器和目录筛选。先执行已有 `python scripts/check_review.py --out .local/review-acceptance`，验证安装 wheel 的 R01–R08；该过程构建并安装一次 wheel。再复用该安装目录：

```bash
python scripts/check_detection.py --package-root .local/review-acceptance/package --out .local/detect-acceptance
```

该运行器检查包中运行文件与源码一致，生成受控录音，使用同一固定版本 Playwright 与 HTTP Range 服务完成桌面／手机 D01–D05：组合筛选和导出、波形／播放／单轨／音量、复核草稿→导出→安装包 CLI 导入→重开、漏检补标、无音频、恶意标签、身份／修订冲突。任何失败、跳过或重试通过均不算完成。需要本机回环端口和 Chromium 启动权限。已有浏览器缓存可用 `PLAYWRIGHT_BROWSERS_PATH` 指定，无需重复安装。

以上是配置执行和数据流程验证，尚未获得原专项脚本或真实业务录音，不证明脚本迁移一致性、真实误报／漏报率或生产容量。

## 配置编辑界面与本机服务

新增 `tests.detect.test_editor` 校验表单导出契约、嵌套条件、未知字段／引用及 JSON 重复字段；`tests.detect.test_editor_server` 使用本机随机端口验证库身份、Host／Origin／会话限制、不可变版本、重启持久化、限定录音输入、检测事务失败回滚和音频 Range。必要端口受限时按实际权限运行，不新增跳过。

`check_detection.py` 现覆盖原 D01–D05 加 E01–E06，桌面／手机共 22 项，复用 `check_review.py` 已构建的安装目录。运行器启动同一安装包的真实 `detect serve`，验证：从原 QA 入口进入编辑器；离线配置导出后经 CLI 校验并执行；图形化指标／AND 条件产生可检索标签；版本冲突、草稿恢复、未保存配置禁止执行；嵌套 JSON 无损往返和无效导入拒绝；新建／示例／导入／复制草稿在刷新、放弃、保存后的状态，以及无已保存版本和旧格式草稿的恢复。任一必需能力缺失、失败、跳过或重试通过都拒绝。服务及临时 HTTP 服务器在测试后退出，不改变用户已有实例。

## 录音质检工作区

`tests.detect.test_workspace` 验证目录重扫、已成功组合的增量跳过、新版本重跑、选定录音试跑、保存后重启、复核与标准同事务、误报原因／明确抽检、修订冲突、冻结后修订隔离、定位不匹配、缺失源录音／无窗口与版本退步。服务组额外验证新 API 的 Host／会话边界、在线数据刷新和报告令牌仅在本机响应中注入。

`check_detection.py` 在原 D01–D05、E01–E06 外增加 W01–W03，桌面／手机共 28 项：

- W01：保存日常组合→批跑→实际播放→在线复核及错误拒绝→未命中正常抽检→重开→新增模式跳过。
- W02：冻结固定集→两个版本比较→确认新版本出现漏检、未自动启用→明确选为日常版本→重新打开和下载明细。
- W03：手动勾选试跑范围→漏检补标→冻结→修订标准→比较仍使用冻结前的范围。

每个工作区用例使用独立的本机服务和合成录音，由同一安装 wheel 执行；测试不修改用户业务数据库，不代表真实业务准确率。运行命令仍使用本节前述 `check_review.py` 和 `check_detection.py`，后者必须复用与最终源码一致的安装目录。
