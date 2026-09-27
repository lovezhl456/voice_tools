# 多 Tab HTML 工作台

从 0.19.1 开始，使用 `voice-tools workbench build` 生成统一 HTML 入口。五个 Tab 与本机导航中的工作台保持一致：检测与复核、整通质检、输出间隙、跨主机任务、使用说明。

```bash
voice-tools workbench build \
  --detection outputs/detection \
  --autoqa outputs/autoqa \
  --gaps outputs/gaps \
  --tasks outputs/tasks \
  --out outputs/workbench
```

打开 `outputs/workbench/index.html`。四个输入都是**已经生成的本地导出目录**；命令不执行检测、模型推理、远程任务或通话。用 `voice-tools --json workbench build …` 获取标准 JSON 封套、入口路径、清单路径和每个 Tab 的状态。

| 参数 | 原生成命令 | 所需入口 |
| --- | --- | --- |
| `--detection` | `voice-tools detect report …` | `index.html` |
| `--autoqa` | `voice-tools qa assess …` | `report.html` |
| `--gaps` | `voice-tools gaps analyze …` | `review.html` |
| `--tasks` | `voice-tools task workbench …` 或 `task review …` | `index.html` |

参数均可省略。省略前三项时，对应标签显示“尚未导入”；省略 `--tasks` 时，离线生成原有任务编排页。使用说明始终随包生成。因此也可以先生成空工作台：

```bash
voice-tools workbench build --out outputs/workbench-empty
```

输出目录必须不存在或为空，不能与任一输入目录相同或互相包含。输入目录缺少入口、包含符号链接或特殊文件时会拒绝；参数失败在输出修改前报告。新页面与 CLI schema、包版本一同纳入安装 wheel。

## 操作与保存

- 每个 Tab 首次打开时加载；切换保留表单、子页和滚动位置。通过同源 HTTP 浏览时，离开标签会暂停页面中的音视频。
- 标签支持左右箭头、Home/End；手机标签栏可横向滚动。URL 的 `#detection`、`#autoqa`、`#gaps`、`#tasks`、`#guide` 可直接定位标签，浏览器前进/后退可切回。
- 刷新后保持当前标签；不承诺保留子页未保存输入。复核、配置和任务仍按原页面按钮导出，需要入库时使用原 CLI 导入。
- “单独打开”可进入原有子页。原单工具 CLI、报告数据格式、试听、下载、标注和校验合同保持不变。

## 可搬移范围

每个明确指定的报告目录会完整复制到输出内，包括音频、JSON、CSV、子页面与资源。不要只移动 `index.html`，应移动整个工作台目录。源目录有更新时，重新生成一个新的输出目录；工作台不会监视源目录，也不会自动写回源数据。

直接打开 `index.html` 可使用外层 Tab 和任务编排。浏览器的 file 协议限制可能影响音频、自动暂停、下载或任务复查中的按需数据请求；需要这些行为时，在目录启动仅本机 HTTP 服务：

```bash
python3 -m http.server 8088 --bind 127.0.0.1 --directory outputs/workbench
```

随后浏览 `http://127.0.0.1:8088/`。工作台无固定部署端口、个人路径和联网资源。原输入 HTML 中本就存在的外部/绝对链接或目录外文件，不会被下载、重写或补齐；正常 CLI 导出的相对资源在复制后继续可用。输入是用户明确选择的可信本地网页导出；命令不解包、执行任务包中的 HTML，也不进入跨主机任务命令目录。

`workbench.json` 记录工具版本以及各标签的 `imported` / `generated` / `empty` 状态，不表示业务判定成功。既有检测报告仍保留各自的合成演示、证据不足及人工复核边界。

验收记录见 [工作台验证](tabbed-workbench-validation.md)，计划与旧能力保留见 [实施计划](tabbed-workbench-plan.md)。
