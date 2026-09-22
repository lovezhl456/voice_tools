# 可配置录音检测、业务标签与复核

`voice-tools detect` 把指标计算、业务条件和人工判断分开：用 JSON 定义检测逻辑，将每次检测保存到本地 SQLite 库，跨批次按标签检索，再把人工确认用于下一版本比较。全部分析在本地 CPU 执行，只依赖项目已有 NumPy；不执行配置里的脚本，不自动安装模型或联网。

本功能对应 issue #34。按用户补充要求，**先定义格式，已有专项脚本后续再转成该格式**。本版附带 `ai-silence`、`low-volume` 两个可执行的格式示例，尚未获得或转换原脚本，不声称与 `ai_silence_scan.py` 判定一致。示例阈值未由业务录音校准。

## 一次完整操作

安装当前版本后，在自己的工作目录执行；下列录音路径需替换为已有文件。输入支持 PCM16 单／双声道 WAV、8–48 kHz、最长 1 小时，并沿用 512 MiB 解码上限；其他格式先用已有 `audio prepare` 流程转换并保留声道。

```bash
# 导出示例，按实际声道与业务参数编辑 JSON
voice-tools detect config-example --kind ai-silence --out ai-silence-v1.json
voice-tools detect config-check ai-silence-v1.json
voice-tools detect config-add ai-silence-v1.json --db detection.sqlite3
voice-tools detect config-list --db detection.sqlite3

# 已保存的版本必须显式指定 id@version。每次重跑用新的批次
voice-tools detect run recordings/ --definition ai-silence@example-1 --batch first --db detection.sqlite3
# 也可以直接引用文件，同时执行多个定义
voice-tools detect config-example --kind low-volume --out low-volume-v1.json
voice-tools detect run recordings/ --config ai-silence-v1.json --config low-volume-v1.json --batch combined --db detection.sqlite3

# 查看批次状态、文件错误和跨批次结果
voice-tools detect batches --db detection.sqlite3
voice-tools --json detect query --db detection.sqlite3 --tag AI无声 --status pending
voice-tools detect query --db detection.sqlite3 --tag AI无声 --tag AI低音量 --tag-mode all --out selected.csv

# 生成本地页面；--include-audio 显式复制经过摘要核对的音频和单轨试听副本
voice-tools detect report --db detection.sqlite3 --out review-first --include-audio
# 本机静态服务，也可以用已有本地站点托管目录；不得把私人录音提交到仓库
python -m http.server 8765 --bind 127.0.0.1 --directory review-first
# 浏览器打开 http://127.0.0.1:8765/ ，填写后“导出复核文件”
voice-tools detect review-import detection-review.json --db detection.sqlite3
voice-tools detect report --db detection.sqlite3 --out review-confirmed --include-audio
```

执行检测时，发现标签退出码为 `1`；它表示检测完成且有发现，不是程序失败。`0` 表示完成且无命中，**不证明质量合格**。配置／身份／参数错误为 `2`；文件错误、缺失声道、没有可用窗口为 `3`，仍保留已完成的结果和错误原因。`--json` 放在 `detect` 前，沿用统一机器封套。

查询支持 `--recording`（录音 SHA-256 前缀或路径关键词）、`--batch`、`--definition`（id）、`--config-version` 和 `--status`。重复 `--tag` 默认匹配任一标签；`--tag-mode all` 在其他条件筛选后，要求**同一批次、同一录音**含全部标签，返回这些标签的条目，不把不同版本的命中拼在一起。查询标签使用当前人工修正后的标签，`automatic` 字段保留原始自动标签与区间；误报可用状态单独筛选。

## 检测定义 1.0

机器可读定义见 [JSON Schema](detection-definition.schema.json)，也可以 `voice-tools detect schema` 输出。`config-check` 另外校验指标引用、区间顺序、唯一规则 id、有限数值和条件复杂度。未知字段／参数直接拒绝，避免拼错后使用默认值。

```json
{
  "schema_version": "1.0",
  "id": "example-low-output",
  "version": "1",
  "name": "输出低电平候选",
  "description": "右轨为输出。仅展示格式，阈值须自行验证。",
  "scope": {"start_s": 0, "skip_first_s": 2, "skip_last_s": 1, "exclude": [{"start_s": 10, "end_s": 12}]},
  "window": {"kind": "sliding", "length_s": 3, "step_s": 1, "include_partial": false},
  "metrics": {
    "output_level": {"kind": "rms_dbfs", "channel": 1, "params": {"remove_dc": true}},
    "output_active": {"kind": "activity_total_s", "channel": 1, "params": {"threshold_dbfs": -65, "frame_ms": 20, "min_duration_s": 0.1, "join_gap_s": 0}},
    "output_quiet_count": {"kind": "silence_count", "channel": 1, "params": {"threshold_dbfs": -45, "min_duration_s": 0.3}}
  },
  "rules": [{
    "id": "quiet-output",
    "label": "AI低音量",
    "description": "有输出活动，但电平低或出现多次持续静音。",
    "when": {"all": [
      {"metric": "output_active", "op": "ge", "value": 0.5},
      {"any": [
        {"metric": "output_level", "op": "lt", "value": -35},
        {"metric": "output_quiet_count", "op": "ge", "value": 3}
      ]}
    ]},
    "unless": {"not": {"metric": "output_active", "op": "gt", "value": 0}}
  }]
}
```

| 字段 | 约定 |
|---|---|
| `id` / `version` | 1–80 字符，以英文字母或数字开头，其后允许点、短横线、下划线；同 id/version 内容不可覆盖 |
| `name` / `description` | 名称必填，业务说明可选；显示用文本，不执行其中的表达式 |
| `scope` | `start_s`/`end_s` 是录音绝对秒数；实际起点取 start_s 与 skip_first_s 的较大值，终点取 end_s 与录音时长减 skip_last_s 的较小值 |
| `scope.exclude` | 绝对时间的排除区间；它们把分析范围切开，指标和窗口不跨越排除区间；允许重叠排除，依次取区间差集 |
| `window` | 见下一节；省略时 `whole` |
| `metrics` | 指标别名到计算定义的映射，每个指标明确 `kind` 和 `channel`，可选 `params`；一个定义最多 32 个指标 |
| `rules` | 最多 32 条，id 唯一；每条包含业务 label 与 when，可选 unless 和说明；多个规则可在同一窗口同时命中 |
| `when` / `unless` | all（AND）、any（OR）、not（取反）；叶节点为 metric/op/value；比较符 lt/le/gt/ge/eq/ne；最多 8 层、128 个节点 |

所有时间单位为秒，`frame_ms` 为毫秒。`channel:0` 是左轨／单轨，`1` 是右轨；不自动推断角色，不将缺失右轨复制成双轨。配置作者负责映射角色，报告明确提示角色未通过工具核实。库中的自动标签只表示规则命中。

### 窗口

| kind | 参数与语义 |
|---|---|
| `whole` | 每个排除后连续范围计算一次，适合累计时长／次数。没有其他参数 |
| `sliding` | length_s 默认 5；step_s 默认等于 length_s；include_partial 默认 false；从每段分析范围起点按步长滑动 |
| `after_activity` | channel 默认 0；params 沿用 activity 指标参数；每个活动片段结束后，加 delay_s（默认 0），检查 length_s（默认 5）的窗口；不截断后续用户续说，可用另一轨指标与 unless 明确排除 |

非整段窗口默认只分析足够长的完整窗口，避免把录音提前结束当作无应答。开启 `include_partial` 后才分析尾部不足窗口。触发轨无活动、范围全部排除或录音不够长时保存 `no_windows`，不会计作阴性样本。最多 10,000 个窗口、100,000 条命中／录音／配置；超限记为错误而不保留截断的命中。

### 内置指标和参数

`voice-tools detect catalog` 输出精确的默认值、范围和单位。所有指标都有 `remove_dc`（布尔，默认 true）；电平指标对整个窗口去均值，活动／静音指标对每一帧去均值。若迁移脚本使用原始电平，请显式设为 false。

| kind | 单位与计算 |
|---|---|
| `rms_dbfs` | 20 log10(RMS)，静音下限 -120 dBFS |
| `peak_dbfs` | 20 log10(绝对峰值)，静音下限 -120 dBFS |
| `duration_s` | 实际窗口秒数，受样本边界量化影响 |
| `clipping_ratio` | 绝对幅值 >= clip_level 的样本比例；clip_level 默认 0.999。检查原始削波时设 remove_dc=false |
| `activity_ratio` / `silence_ratio` | 活动／静音片段累计长度除以实际窗口长度 |
| `activity_total_s` / `silence_total_s` | 片段累计秒数 |
| `activity_longest_s` / `silence_longest_s` | 最长片段秒数；不存在时为 0 |
| `activity_count` / `silence_count` | 片段次数 |

活动／静音参数：frame_ms 默认 20（5–100）；threshold_dbfs 默认 -45（-120–0）；min_duration_s 默认 0.1（0–3600）；join_gap_s 默认 0（0–120）。每帧 RMS **大于等于阈值**视为活动，低于阈值为静音。尾帧按实际样本计算，不补零；先移除短于 min_duration_s 的片段，再合并间隔 <= join_gap_s 的片段。**合并后的片段时长包括被桥接的小间隙**，迁移时需核对是否与原算法相同。能量活动可能来自噪声、音乐或提示音，不证明有效回答。

累计条件写法例如：whole 窗口 + `silence_total_s >= 10`；次数条件例如 sliding 30 秒 + `silence_count >= 3`；持续条件用 `silence_longest_s >= 2`。每条命中保留窗口起止、全部实际指标值及单位、规则条件和说明；不会把整段累计命中伪装成单个短静音的位置。

### 后续迁移专项脚本

1. 确认原脚本输入、声道、预处理、帧尺寸、阈值比较边界、短段过滤和合并顺序。
2. 将已有指标映射到 `metrics`，将时序范围映射到 scope/window，将业务组合映射到 rules。
3. 如果计算能力缺失，扩展 `tools/detect/metrics.py` 的注册表与 `measure`，补充参数契约和已知信号的预期值测试，再由配置引用新 kind；不要把源码字符串放进 JSON。算法语义发生变化须升级实现版本和配置版本。
4. 在相同录音、相同参数下比较原脚本和本工具的逐窗口指标、标签、命中边界，包括正常／异常／边界样本。只有完成这一环节才能声明迁移一致。

## 保存、修正与比较

录音以文件 SHA-256 为身份，相同内容的不同路径只分析一次并保留来源列表；文件内容变化后成为新身份。每批次同一个定义 id 只能选择一个版本；同时保存工具版本和检测引擎版本 `detection-1`。配置规范化后保存全文和摘要，省略默认值与显式默认值具有同一身份。

检测库保存：定义；录音身份／来源／波形；批次；每个录音和定义的执行状态（包含零命中、错误和无窗口）；自动标签；独立的人工复核事件。重跑创建新结果，**不会复制人工结论为新版本自动确认，也不会删除旧结论**。

页面沿用共享双轨波形、时间轴、缩放、显示增益、单轨试听、循环、音量和静音；目录／录音筛选与模糊检索也复用原组件。波形随库保存，无音频仍可查看；试听副本来自摘要匹配的原文件，源文件移动或变化后显示不可试听，不能悄悄加载不同内容。报告是快照，新检测或复核入库后需生成新目录。

人工状态为 pending、confirmed、false_positive、corrected。修改标签或时间必须使用 corrected。每次复核包含复核人、备注、修正后的标签／范围，并追加修订号和时间。CLI 示例：

```bash
voice-tools detect review FINDING_ID --expected-revision 0 --status confirmed --reviewer reviewer --db detection.sqlite3
voice-tools detect review FINDING_ID --expected-revision 1 --status corrected --label AI低音量 --start 3 --end 5 --comment 已试听 --reviewer reviewer --db detection.sqlite3
voice-tools detect history FINDING_ID --db detection.sqlite3
```

页面导出的 `detection-review.json` 是 `schema_version:1.0`、`library_id`、`reviews`、`manual` 组成的对象。reviews 条目包含 finding_id、expected_revision、status、reviewer、label、start_s、end_s、comment。manual 条目包含唯一 id、batch_id、recording_id、config_hash、label、start_s、end_s、reviewer、comment（漏检原因必填）。漏检可关联零命中的已有检测记录，通过页面底部或 `manual-add` 命令追加。

复核导入是**整包事务**：库身份错误、过期修订号、无效时间、重复补标或未知录音／配置时整包回滚，不覆盖别人的复核。导入成功后重新生成报告；原报告中的旧草稿不能再次当作新结论导入。

```bash
voice-tools detect config-export ai-silence@example-1 --db detection.sqlite3 --out ai-silence-v2.json
# 修改 version 为 example-2，并修改所需参数
voice-tools detect run recordings/ --config ai-silence-v2.json --batch second --db detection.sqlite3
voice-tools detect compare --db detection.sqlite3 --definition ai-silence --before first --after second --out comparison.json
```

对比以“录音 SHA-256 + 原始自动标签”为单位，列出新增、移除和不变；同时列出仅在一边出现的录音，以及两边状态不是 ok 的不可比较项。错误／无窗口不会被当作新版本消除了问题。

误报消除、新误报、漏报补回和新漏报只依据**这两个批次中已经存在的人工证据**分类；人工确认和误报结论冲突时单列冲突，不擅自选择其一。时间段变化保留在原始结果中，此命令不证明两个片段位置等价，也不把未复核结果推断为准确率。需先把两边人工结论导入库，再重新执行 compare。

SQLite 库及报告包含录音路径、业务标签和人工信息，请保存在本地数据目录。`--hide-paths` 只影响报告展示和快照中的源路径，不会修改库中的溯源信息。JSON 导出保留原文；CSV 对可能触发表格公式的文本加前导单引号。无需额外运行服务、账号或云端存储。
