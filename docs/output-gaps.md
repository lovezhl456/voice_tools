# 输出中途间隙：检测、试听与旁证

[返回工具导航](../README.md) · [数据契约](output-gaps-contract.md) · [验证记录](output-gaps-validation.md)

`voice-tools gaps` 独立寻找 AI 已开始讲话后出现的可疑长停顿、短断音、密集短断音和有事件依据的永久中断。`qa` 仍负责用户说完后没有回答或回答过晚。两种结果和人工标签互不冒用。

## 安装与输入

基础功能沿用 Python 3.9+ 和 NumPy：`python -m pip install -e .`。不使用 hiccup、不加载模型、不连接网络。WebRTC VAD 为可选 `.[vad]`；导入 RTP/NISQA 已有结果无需安装 tshark、PyTorch 或模型。生成这些证据仍需对应工具原有依赖及授权。

输入为 8–48 kHz PCM16 WAV，单文件最长 3600 秒、解码样本最多 512 MiB。单声道可以读取，但结果为证据不足。其他格式先用 `voice-tools audio prepare`，保持真实声道，不将混音复制成双轨。格式准备的时间映射未核实且存在事件时，会保留原事件作旁证，仅进行声学检测。

默认左轨用户、右轨 AI；同名 `.events.json` 中的角色优先。`--system-channel` 显式选择与事件冲突会报错。`--channels-verified` 只在已通过受控通话核实角色后使用。

```bash
voice-tools gaps analyze call.wav --out outputs/gaps-001 --include-audio
voice-tools --json gaps analyze calls/ --system-channel 1 --out outputs/gaps-002
voice-tools gaps analyze call.wav --events call.events.json --out outputs/gaps-003
voice-tools schema --tool gaps
```

`--events` 只适用于单个文件；目录递归扫描 WAV，自动读取各录音同名事件。每次使用新输出目录。

## 结果与阈值

| 类型 | 默认规则 | 解释边界 |
|---|---|---|
| dead_air | 前后有 AI 活动，中间至少 800 ms | 自然停顿、业务等待也可能符合 |
| micro_dropout | 50–300 ms 的近静音，两侧有活动 | 不证明 RTP 丢包；噪声/PLC 可遮盖空洞 |
| clustered_short_gaps | 1.5 秒内至少三个短断音 | 引用短断音成员，不重复计入候选数 |
| terminal_interruption | 明确应继续输出，已有输出，随后无声且完整观察到阈值 | 没有播放预期或录音提前结束时不给故障结论 |

可调整 `--dead-air-ms`、`--micro-min-ms`、`--micro-max-ms`、`--cluster-window-ms`、`--cluster-min-count`。长停顿使用去直流偏移后的固定能量门限，默认 `--threshold-db -45`；短断音使用 5 ms RMS，默认 `--near-silence-db -60`。可选 `--backend webrtcvad` 影响活动检测，要求其支持的采样率。

没有 TTS 事件时，活动不能证明同一句话或相同业务轮次，结果标为 acoustic_only。单轨、重复声道和全静音为 INSUFFICIENT_EVIDENCE。输出状态包括 CANDIDATE、EXCLUDED、CENSORED；无候选不是通话通过。不给“故障概率”。

用户开始讲话后，本候选的观察窗口在用户讲话起点结束；如果用户在间隙开始后 150 ms 内开口，整个间隙按轮次改变排除。较晚反应保留此前已发生的沉默；之后的应答延迟由 qa 判断。经验证的工具等待仅在 allows_silence=true 时过滤。TTS 播放分片共用 utterance_id，不把分片 EOF 当成整个回答完成。

## 试听、标注与片段

输出包括 `results.jsonl`、`summary.csv`、`run.json`、`report.html`、`review.html`、`review.csv`。

`--include-audio` 复制原录音和分轨供离线试听；不启用时仍可看波形。复核页支持左右轨、缩放、循环、定位、CSV 导入导出和手工补标。波形是摘要，显示分辨率不一定足够看清 50 ms 空洞；候选边界和下载片段使用原采样时间。

1. 打开 `review.html`，核对角色、候选及旁证，试听。
2. 选择确认异常、正常停顿、业务排除或无法判断，填写复核人，记下标注。
3. 若发现漏检，调整试听起止，点击“将当前试听范围补标为漏检”。它不会改写检测结果。
4. 导出 CSV，再运行：

```bash
voice-tools gaps review-check gaps-review.csv --results outputs/gaps-001/results.jsonl
```

刷新/离开前必须导出。导入标签只接受相同录音和检测身份；新参数或事件改变后需要重新核对。新标签不能交给 `qa promote/evaluate`。本版交付复核数据与机器校验，不提供未经真实标签标定的准确率。

片段按需在浏览器从附带 PCM16 WAV 导出；时间映射 JSON 保留原录音起止、摘要、间隙 ID 和采样率。若浏览器限制 file:// 下读取，请用支持 HTTP Range 的本机静态服务打开该结果目录（本地 8080 示例已验证）；不支持 Range 的服务可能无法定位或循环。不要对真实录音目录开启公网服务。

## RTP / NISQA 关联

```bash
voice-tools report build --pcap-group edge call.pcap --rtp-port 16000 \
  --rtp-timeline --out outputs/media
voice-tools nisqa analyze call.wav --channel right --provenance --out outputs/nisqa
voice-tools gaps analyze call.wav --evidence evidence.json --include-audio --out outputs/gaps-linked
```

关联清单格式见数据契约。RTP 必须显式指定采集点、方向、SSRC 和录音零点对应的 epoch；不按第一个包猜测录音开始。每个采集点单独关联，SSRC 变化可增加明确绑定。时钟漂移或抓包时间倒退时不给窗口级已对齐结论。

NISQA 新增 `--provenance` 仅额外写溯源文件，默认评分格式和流程不变；它额外读取源文件计算摘要。权重许可、显式下载和 CPU 依赖沿用 [NISQA 文档](nisqa.md)。旧评分结果可通过 source_file+声道显式绑定，标为未验证；缺少 RTP/RTCP 或 NISQA 不影响纯录音检测。旧 RTP 报告只展示整流统计。

不把断音与同时间的序号跳跃、评分变化自动归因为网络、TTS 或 LLM。

## 可复现示例与跨机使用

```bash
python scripts/gaps_demo.py --out outputs/gaps-demo
voice-tools report build --pcap-group demo-edge outputs/gaps-demo/synthetic.pcap \
  --rtp-port 16000 --rtp-timeline --out outputs/gaps-demo/media
voice-tools gaps analyze outputs/gaps-demo/calls --evidence outputs/gaps-demo/evidence.json \
  --include-audio --out outputs/gaps-demo/review
voice-tools task pack outputs/gaps-demo/task.json --root outputs/gaps-demo --out outputs/gaps-task.vtask.zip
voice-tools task run outputs/gaps-task.vtask.zip --out outputs/gaps-task-run
voice-tools task collect outputs/gaps-task-run --out outputs/gaps-result.vresult.zip
voice-tools task review outputs/gaps-result.vresult.zip --out outputs/gaps-task-review
```

示例是合成音调和合成 PCAP。NISQA 示例仅提供无分数的时间片段，reason 明确标记未运行模型，不能作为听感评分证据。expected.json 中的规则预期独立编写。

`evidence-legacy.json` 演示旧 RTP 汇总和未验证 NISQA；`evidence-nisqa.json` 可单独演示评分片段关联；`evidence-wrong.json` 演示错误采集点，退出码为 3，但检测结果保留。跨机路径必须落在打包根目录，引用目录结构随包保留；无需把模型、PCAP 或原始执行机路径自动附加到 gaps 运行环境。

## 演示页部署

仓库中的 `docs/output-gaps-demo/index.html` 是使用入口，主按钮指向已提交的指南与生成器。仓库不附带生成的录音或任务复查产物。先执行上一节命令，再从仓库根目录组装静态目录：

```bash
mkdir -p outputs/gaps-site
cp docs/output-gaps-demo/index.html outputs/gaps-site/index.html
cp -R outputs/gaps-demo/review outputs/gaps-site/demo
cp -R outputs/gaps-task-review outputs/gaps-site/task-review
```

每次使用新的目标目录，避免混入旧结果。将 `outputs/gaps-site/` 部署到支持 HTTP Range 的静态服务，映射为 `http://127.0.0.1:8080/voice-output-gaps/`，页面的“已部署的本机演示”链接才可使用。使用其他地址时，直接打开该目录中的 `demo/review.html`、`demo/report.html` 和 `task-review/index.html`。只有这里生成的合成示例适合接入公共本地导航，不要发布真实录音。

## 退出码与排障

| 退出码 | 含义 |
|---|---|
| 0 | 批次完成，可包含候选或证据不足 |
| 1 | 设置 --fail-on-findings 后存在独立候选 |
| 2 | 全局参数、标签或整体输入无效 |
| 3 | 某录音或显式关联证据处理失败；其他结果保留 |

优先检查 run.json 的 errors、逐录音 error 和 evidence.sources 中的状态。没有事件保持声学等级；没有匹配绑定为 not_bound；旧汇总、未对齐、部分时序均不被解释成“没有异常”。

参考问题定义：[hiccup 固定版本](https://github.com/AhmadIbrahiim/hiccup/tree/260c92e867203f192dd8d5c37879b4e624ec3f11)。本实现独立编写；未复制上游代码、测试、UI，未引入运行依赖。
