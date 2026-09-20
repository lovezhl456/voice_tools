# 跨主机任务与结果复查

版本：0.12.1。任务由本机编排，Docker 执行端完成业务操作；完整结果包带回后离线复查。Studio 不承担远程控制或后台呼叫服务。

```mermaid
flowchart LR
  A[模板与顺序步骤] --> B[CLI 收集素材与校验摘要]
  B --> C[vtask.zip]
  C --> D[执行机离线检查]
  D --> E[后台 Docker 执行]
  E --> F[vresult.zip]
  F --> G[校验与离线复查]
  G --> H[录音 / 断言 / 评分 / 人工标签]
```

## 1. 执行端准备

需要 Docker、宿主机 Python 3.9+（只用于 `vt` 控制脚本）和当前版本仓库。准备期间可联网，日常命令使用本地镜像，`--pull=never`，不会自动安装依赖或下载模型。

```sh
./vt prepare                         # 当前架构
./vt prepare --platform linux/amd64   # 在对应架构机器准备，或使用 Docker 仿真
```

统一镜像 `voice-tools-executor:0.12.1` 使用 Python 3.11，包含现有业务 CLI、FFmpeg、tshark、SIPp、PJSUA2、NISQA CPU 依赖和固定源码版本的 ViSQOL 后端。ViSQOL 自带评分模型与许可随后端导出；NISQA 权重独立准备、校验并挂载。镜像不包含录音、账号、SSH 私钥或用户凭据。Bazel/PJSIP 编译可能持续较长时间；构建成功不等于目标线路通过。

NISQA 的权重使用要求见 [NISQA 文档](nisqa.md)。准备好权重目录后设置 `VT_MODELS_DIR`；`executor.json` 的 `model_dir` 使用容器路径 `/models/nisqa`。需要首次下载时显式使用现有 `voice-tools nisqa download`，不要把模型放进任务素材目录。

不同 CPU 架构分别准备镜像。可用 `VT_IMAGE` 指定不同的本地镜像名；开发时 `VT_NATIVE_IMAGE` 可复用同架构、同 Python 3.11 的已验证 native 构建阶段，默认仍从源码准备。向不联网的同架构执行机转移环境，可用 `docker save voice-tools-executor:0.12.1 -o executor-image.tar`，目标机执行 `docker load -i executor-image.tar`。同时复制仓库中的 `vt` 与 `scripts/task_host.py`，或完整仓库。记录实际 image ID；任务包和模型分别传送。

| 宿主环境 | 本轮设计范围 | 网络条件 |
|---|---|---|
| macOS + Docker | 录音/QA/音质、SIP 功能拨测、SSH 远端采集、HOMER | 普通 SIP 需 Desktop 4.34+、启用 host networking、`VT_NETWORK=host VT_DESKTOP_HOST_NETWORK=1` |
| Linux + Docker | 上述能力及 SIPp 原始包回放/接口采集 | SIP 使用 `VT_NETWORK=host`；镜像为 SIPp/dumpcap 配置 CAP_NET_RAW |
| 原生 Python | 同一 task CLI；依赖由本机提供 | 无 Docker 后台管理；运行应交给自己的服务管理器 |

Mac Docker host 网络只处理 TCP/UDP，不能提供 Linux 主机网卡/原始包能力。Mac 的 `bind_address` 使用 `0.0.0.0`，`public_address` 填实际可达地址。实际 SIP/RTP 端口、防火墙、NAT 和远端抓包权限需在目标机验证。本机 Docker Engine 23.0.5 不用于证明新版 Mac host 网络通话能力。

## 2. 本机编排和打包

```sh
voice-tools task workbench --out outputs/task-workbench
# 浏览器打开 index.html：选择模板、顺序步骤、侧栏参数，导出 task.json。
./vt task pack task.json --root ./workspace --out task.vtask.zip
```

页面只导出任务说明，不通过浏览器后台发起呼叫。CLI 收集实际素材和跨文件引用，计算 SHA-256。所有输入必须位于 `--root` 内，目录应只包含本次素材。工作台提供全量业务命令目录；账户初始化、登录、权重下载属于执行机准备，不放进任务。

输入文件使用明确引用，输出位置由执行器管理：

```json
{
  "schema_version": "1.0", "id": "call-review", "title": "通话录音复查",
  "inputs": {"audio": "assets/call.wav"},
  "steps": [
    {"id": "health", "tool": "audio", "action": "inspect", "params": {"inputs": [{"input": "audio"}]}},
    {"id": "quality", "tool": "nisqa", "action": "analyze", "params": {"inputs": [{"input": "audio"}], "channel": "both"}}
  ]
}
```

`{"step":"analyze","path":"data/results.jsonl"}` 引用前序产物，目录引用支持通配符。`pcap_group` 使用 `[["sensor-a", {"input":"capture1"}, {"input":"capture2"}]]`。显式依赖用 `depends_on`；每步时限 `timeout_s` 默认为 3600 秒。整体顺序执行，业务并发使用 SIP batch 或 SIPp 本身。

SIP scenario 的播放文件、media.json 音频、批量队列、ViSQOL 配对 CSV、QA 结果/冻结清单和会话索引的本地源文件会继续收集。输入保留原件，只重写执行工作副本中的已声明绝对路径。QA 样本 ID 和音频哈希保持原值；任务迁移不自动更改人工标签身份。

空目录、符号链接、缺失引用、模型文件、已识别的私钥/凭据和越出根目录的依赖会被拒绝。JSON/JSONL 结构化输入上限为 16 MiB，超过时需拆分，不能静默遗漏依赖。ZIP64 上限为 100000 文件、100 GiB 展开数据；工作目录、原件和结果包还会额外占用磁盘。

## 3. 执行机配置与运行

复制 [executor.example.json](../examples/task/executor.example.json) 为 `executor.json`。配置放在当前工作目录内；密码只能通过环境变量提供。SSH 密钥、known_hosts、HOMER 配置可放在指定执行机目录，通过 `VT_EXECUTOR_DIR` 只读挂到 `/executor`，配置中引用容器内路径。密钥不会由打包器自动收集。

```sh
export VT_MODELS_DIR=/absolute/path/to/nisqa-models
export VT_EXECUTOR_DIR=/absolute/path/to/executor-config
./vt task check task.vtask.zip --profile executor.json
./vt task run task.vtask.zip --profile executor.json
# 返回 run-xxxxxxxxxxxxxxxx；随后可以断开终端。
./vt task status run-xxxxxxxxxxxxxxxx
./vt task logs run-xxxxxxxxxxxxxxxx
./vt task stop run-xxxxxxxxxxxxxxxx
./vt task collect run-xxxxxxxxxxxxxxxx --out result.vresult.zip
```

`check` 校验包、版本、引用、参数、SIP 场景和本地依赖，不拨号，不查询 HOMER，不连接 SSH。来自前序步骤的文件还不存在时，实际执行前再解析；预检不证明网络可达或服务认证通过。执行版本须与任务包一致。

默认 `network_allowed=false`，容器使用 `--network none`。确需业务网络时在执行机 profile 中设为 `true`；SIP 同时设置 `VT_NETWORK=host`。不自动重试呼叫或其他业务网络操作。环境使用 `environments.<name>.params.<tool>` 设置该工具允许的执行端参数；SIP 的 `sip` 覆盖仅允许目标、账号、端口、codec 和时限，不能改动作或断言。选择环境的任务步骤填写 `environment`。

运行保存于当前目录 `.vt/runs/RUN_ID`，容器名称为 `vt-RUN_ID`。`vt` 在宿主机控制 Docker，业务容器不挂载 Docker socket。停止容器会先发出正常中断；已落盘证据保留。远端环形抓包独立运行，停止本地容器不会自动停止远端环形任务，必须用对应 `ring-stop`/`ring-fetch` 步骤处理。

原生用法：`voice-tools task run task.vtask.zip --profile executor.json --out outputs/run-001`。原生 status/logs/collect 接受运行目录；Docker `vt` 对这些操作接受运行编号。

## 4. 带回结果与复查

```sh
./vt task review result.vresult.zip --out review
# 用浏览器打开 review/index.html；音频定位推荐使用本地 HTTP 服务。
```

结果包包含任务原包、原始输入、工作副本、实际产物、日志、调用参数、版本/镜像 ID、运行状态和文件摘要。执行机 profile 不进入结果包；声明的秘密环境变量值会从进程 stdout/stderr 中脱敏。任务输入本身仍可能有业务敏感数据，按实际接收范围传递。

页面提供批次状态、步骤断言、事件记录、录音波形、左右声道、片段循环、NISQA/ViSQOL 评分、质检/协议原始字段、文件下载和来源追溯。人工笔记与人工标签需要显式导出；标签 CSV 可交给现有 `qa promote`，不会自动把模型结论晋升为黄金集。

为控制浏览器负载，逐步 JSONL 预览最多 2000 行、音频索引最多 2000 文件，最多为前 128 个且不超过 64 MiB 的 PCM16 WAV 生成波形；大文件仍保留原件。归档内 HTML 和脚本只提供下载，复查页不会执行它们。

状态分别保留：`completed`、`findings`、`insufficient_evidence`、`partial`、`failed`、`interrupted`、`skipped` 和 `remote_running`。结果有候选问题不等于执行失败；未接收足够证据不等于断言通过。依赖失败的步骤跳过，独立步骤继续。NISQA 是非侵入式预测，ViSQOL 需要同一句原声与待测音频，均不能直接证明故障原因或人工 MOS。

## 5. 不需要外部设备的验收示例

```sh
./vt task pack examples/task/demo.json --root . --out demo.vtask.zip
./vt task run demo.vtask.zip
./vt task collect RUN_ID --out demo.vresult.zip
./vt task review demo.vresult.zip --out demo-review
```

示例生成合成录音、分析候选问题并检查格式。它能验收打包/迁移/执行/复查流程，不证明真实录音准确率、SIP 线路、HOMER 服务、SSH 采集或执行机容量。实际检查结果见 [本版验证记录](task-validation.md)。

## gaps 依赖与可信复核

gaps 的 evidence 清单、NISQA provenance 与其原结果、RTP timeline 与分片由打包器显式收集，必须落在打包根内。新类型证据及其引用的内容保持字节和摘要，执行路径映射单独处理；不可重写后重新计算摘要冒充原始证据。每片 <=8 MiB，沿用结构化文件 16 MiB 和总包额度。RTP 包行不进入普通记录列表。结果页“人工标签”重建 gaps 复核页，独立导入/导出间隙标签；包内 HTML 不执行。示例命令见 [间隙使用指南](output-gaps.md)。
