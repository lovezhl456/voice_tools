# latency 引擎与任务集成协议

运行参数和退出码由 [contract.py](../src/voice_tools/tools/latency/contract.py) 维护，CLI schema 导出 `latency_contract`；[用户说明](latency.md) 与 [安装说明](latency-install.md) 引用它，不另设参数表。

## 边界与固定来源

`cli → latency/service → isolated runtime → prefix/worker.py → pinned detector.py`。主解释器只读取 WAV 头、计算身份、写结果及报告；引擎用最终前缀的 Python 以 `-I` 启动。每文件串行启动独立进程，逐块加载到一份双声道 float32 数组；线程设置和缓存不回写父进程。输入长度是数组预检，不等于峰值 RSS 上限；每文件结果记录引擎实际 RSS。

上游为 SignalWire latency_checker `0d71ca1f42a59744258420e4074ae0378f86c857` / MIT。包内 [manifest](../src/voice_tools/tools/latency/resources/manifest.json) 固定源码归档和检测器 SHA-256、补丁版本及资源摘要；[补丁差异](../src/voice_tools/tools/latency/resources/detector.patch) 可直接审阅。

适配版只改变三类内容：将双向配对公共常量移到短 AI 过滤之前，消除未初始化异常；去掉 10s/30s 正延迟上限；增加片段的资格和去向记录。保持起声、静音、串音抑制、边界修剪和双向配对规则。包装层明确声道、计算可信统计和不足证据状态，不使用上游无配对时的零统计。

补丁 2 修正第三类中的续说归属：AI 在人声暂停中起声、人声在 600ms 内续说时，续说段记录 incoming overlap；先前人声来源段记录 outgoing overlap，续说段自身的 outgoing 仍由它之后是否收到 AI 回复决定。人声持续期间被 AI 打断的情形仍标记当前人声 outgoing overlap。此修正不改变配对或延迟数值，结果协议仍为 1.0；manifest 的补丁版本与摘要随修复更新。

## Worker 与结果版本

worker 从系统临时目录读取 JSON 请求：`input`、`system_channel`、有效 `parameters`；写独立响应 JSON，stdout 仅返回写入确认。响应 `protocol_version=1.0`，含检测片段、双向配对、波形、音频摘要和资源记录。缓存/协议暂存不在结果收集树。失败 stderr 有界保留；进程超时、SIGINT/SIGTERM 会终止整个引擎进程组。

公共文件有独立 `kind=latency_run/latency_recording` 与 `schema_version=1.0`，通用 CLI 封套仍用既有 1.0。`run.json` 不内嵌全部详情；逐文件 JSONL 引用按录音保存的 JSON。录音身份与路径解耦：文件 SHA-256 为 recording_id，PCM SHA-256 检测不同路径/头信息下的同内容。输入在分析期间变动则该文件失败。

`measurement.segments` 每段包含 `id/speaker/start/end/duration/full_duration/eligible/incoming/outgoing`。`full_duration` 是上游未修剪时长，用于最短片段资格；`duration` 是修剪后的范围。去向包括 `short_filtered/overlap/superseded/unpaired/paired`，incoming 和 outgoing 分别描述它作为前一段的应答和后一段的来源，不能混为同一方向。

Human → AI 覆盖分母取全部 eligible human，分子取完成的 Human → AI 配对。不把重叠或被后续片段替代的合格人声从分母剔除；零分母为 null。重复内容每条处理记录参与统计并显式提醒。无有效轮次不产生零延迟。分位数在全部有效轮次上用线性插值计算。

时间戳以当前录音起点为零；不对齐或覆盖 benchmark 的桥时钟，也不修改黄金集身份和业务规则。

## 可运行任务

仓库 `python scripts/latency_demo.py --out demo` 同时生成确定边界的 WAV、`task.json`、`expected.json` 和 `executor.example.json`。执行机配置路径必须按实际执行机器修改：

```json
{"schema_version":"1.0","network_allowed":false,"latency_dir":"/opt/latency","environments":{}}
```

```sh
voice-tools task pack demo/task.json --root demo --out demo.vtask.zip
voice-tools task check demo.vtask.zip --profile executor.json
voice-tools task run demo.vtask.zip --profile executor.json --out task-run
voice-tools task collect task-run --out demo.vresult.zip
voice-tools task review demo.vresult.zip --out task-review
python scripts/serve_latency.py --root . --port 8088
```

`latency_dir` 是 runtime 参数，不作为输入打包。只有 latency 步骤检查该依赖，旧配置不要求填写。执行机 profile 按已有覆盖规则将实际配置转换为 CLI 参数；之后 CLI 参数优先于环境变量。安装器不是业务任务命令。

新版继续读旧任务、配置及结果；旧版未注册 latency 时拒绝新命令，不将其误判为成功。文件 timeout 与步骤 timeout_s 并存；以先触发者为准。批次文件错误保留成功产物并返回部分失败，取消沿用任务的 interrupted 语义。

任务包收集原输入，结果包保留工作输入和步骤产物；独立报告只有 include_audio 才复制音频。任务复查根据已校验包内路径重建试听引用，不需要原素材绝对路径。包内 HTML/脚本只供下载，不执行；latency.js/css 来自当前安装包，数据通过同源 JSON 获取。通用复查最多展示 2000 条逐文件索引，并明确标记截断，完整 files.jsonl/turns.csv/详情仍可导出。每页 50 轮及波形降采样不改变统计。

摘要校验只保证包内字节完整，不证明 JSON 字段类型可信。共享展示组件在运行摘要和录音详情两处校验配对数、分母、双向轮数及去向计数为非负安全整数，覆盖率为 0–1 有限数值或 null；不接受数字字符串、布尔值或缺失计数，也不把非法值替换为 0。文本插入 HTML 前转义，非法统计显示字段错误。原始导出数据和测量算法不变。

升级工具不会更新已经生成的静态报告。任务结果包需使用修复后的工具重新执行 `voice-tools task review result.vresult.zip --out task-review-fixed`，输出到新目录；独立报告重新执行原 analyze/batch 命令并选择新的 `--out`。保留原结果包作为证据，不修改其校验清单或包内历史 HTML。此页面修复不改变引擎补丁 2，无需重装引擎前缀。

## 共享模块影响

仅新增工具注册、schema 合同、任务 runtime/依赖识别、可选 profile 字段、复查入口和打包资源。复查 CSP 的 connect-src 从 none 扩为 self，以按需获取本页同源结构化 JSON；仍不执行包内 HTML/脚本。默认 Dockerfile 未引入引擎。许可、版本号及文档索引为配套变动。QA/SIP/benchmark/NISQA/ViSQOL 业务文件的基线对照及实际回归见 [验收记录](latency-validation.md)。
