# 录音波形组件选择与 UI 验收

核对日期：2026-09-19。复核页由手绘 Canvas 改为 WaveSurfer.js 7.12.12，继续离线运行。

## 社区组件比较

| 组件 | 官方能力与定位 | 本项目判断 |
|---|---|---|
| [WaveSurfer.js](https://github.com/katspaugh/wavesurfer.js) | 分声道波形、预计算峰值、HTMLMediaElement、Timeline 与 Regions 插件；BSD-3-Clause | 选择。能复用现有播放源和 Python 波形摘要，无需 React 或新服务 |
| [BBC Peaks.js](https://github.com/bbc/peaks.js) | 波形概览/缩放视图、点和区间标记、多声道；LGPL-3.0 | 长音频标注很合适；需要另一套波形数据/组件集成，本次不引入 |
| [Waveform Playlist](https://github.com/naomiaro/waveform-playlist) | 多轨编辑、片段移动/裁剪、效果与播放；提供 React/Tone.js 及 Web Components 方案；MIT | 编辑能力超出当前复核范围 |

以上来自官方仓库、文档与示例；组件选择不代表已对三个库做完整性能基准。

## 实现与来源

- 固定 [7.12.12 发布版](https://github.com/katspaugh/wavesurfer.js/releases/tag/7.12.12)，从官方 npm tarball 取得 core、Regions、Timeline 的 UMD 文件，校验 npm 的 SHA-512 integrity。
- 文件、版本、来源与 SHA-256 位于 `src/voice_tools/tools/recording_qa/vendor/manifest.json`；BSD 许可证在相邻的 `wavesurfer.LICENSE.txt`。生成 HTML 时内嵌组件和许可证，无 CDN 请求。
- 左右轨来自原录音的独立声道，共用一个播放器与时间轴。单声道只显示一轨；声道选择使用已有逐轨试听副本。
- 使用已有 min/max 波形摘要，不要求浏览器再次解码整份录音。摘要分辨率显示在图下；放大视图不增加原始数据细节。
- 关闭自动归一化。显示增益只影响图形，左右轨按同一比例缩放，不改变音频、分析参数、片段或标注窗口。
- 标注窗口固定，试听范围可拖拽或键盘调整；额外数字输入和时间滑块提供可访问的操作入口。

## 录音参考

只用合成恒定音调无法验证自然信号的细节。本次另外取回 WaveSurfer 官方样本，在独立本地参考页对照，不生成业务标签：

| 官方原文件 | 原始参数 | 处理 |
|---|---|---|
| [audio.wav](https://github.com/katspaugh/wavesurfer.js/blob/7.12.12/examples/audio/audio.wav) | 16 kHz、2 声道、26.39 秒 | 仅转 PCM16 试听，保留采样率、声道和幅度 |
| [demo.wav](https://github.com/katspaugh/wavesurfer.js/blob/7.12.12/examples/audio/demo.wav) | 22.05 kHz、1 声道、21.77 秒 | 保持单轨，仅转 PCM16 |

这些文件是社区原始音频素材，官方示例未单列录音者及内容来源；不能将它们称为已核实的真实通话。原始摘要与参数留在本地证据目录，媒体没有提交仓库或向公网发布。

另已定位 WaveSurfer 的 LibriVox 朗读和 BBC Peaks 的 Tears of Steel 对白素材；其进一步下载未完成（自动审批超时）。在取回、核对出处并试听前，不将其记为已通过的人声/对话验收。

## 验收结果

- 桌面与手机：全部 19 条机会列表保持可读，页面无横向溢出；双轨共用时间轴、单轨不显示假右轨。
- 放大/缩小、全长、定位当前机会、显示增益、鼠标拖动及键盘调整试听边界通过。
- 播放推进、单轨切换保留位置、7.1–7.6 秒区间循环通过；无附带音频时保留波形并禁用播放。
- 快速切换记录、筛选、未保存表单保护、CSV 导出重导入通过；浏览器控制台错误为 0。
- 真正浏览器拖动曾发现区间手柄继承了不接收指针事件的样式，已修复并重验。非整百分秒录音的试听终点改为向下取整，避免超过实际时长。
- QA/CLI 的 13 项定向测试通过；新增检查生成页包含组件与许可证且不引用外部脚本。

这验证的是显示和交互。当前素材不能用于报告真实通话准确率、模型回答质量或真人听感结论。
