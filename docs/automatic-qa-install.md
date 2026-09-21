# 整通自动质检：依赖安装与排障

`voice-tools qa setup` 把 CPU 运行库、固定模型下载和就绪检查串成一个入口。无需 GPU、Torch 或单独启动模型服务。安装过程不需要录音。

## 首次安装

在仓库根目录创建并激活虚拟环境，再安装基础 CLI；已安装的用户可直接运行 `qa setup`：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
voice-tools qa setup
```

菜单输入 `1` 安装全部，`2` 只安装运行库，`3` 只下载／校验模型，`0` 取消。空输入和未知选项不会触发安装。菜单会显示实际 Python 路径；使用 `python -m voice_tools qa setup` 可明确指定当前 Python。

一条命令完成全部准备，不显示菜单：

```bash
voice-tools qa setup --component all
```

| 选择 | 操作 |
|---|---|
| `all` | 安装运行库 → 下载／校验模型 → 新进程真实 CPU 推理检查 |
| `runtime` | 安装 NumPy `>=1.24,<3`、ONNX Runtime `>=1.18,<2`、SciPy `>=1.10,<2`；新进程验证导入与 CPU provider |
| `model` | 下载固定 Silero VAD 6.2.2 ONNX 权重（约 2.3 MB，MIT），校验大小和 SHA-256；已有正确文件直接复用 |

基础 Python 要求 3.9+，安装器只选择兼容当前 Python／系统架构的二进制 wheel。pip 使用当前 CLI 的 `sys.executable -m pip`，保留既有满足约束的版本；不自动升级到最新版，不调用系统 sudo，不绕过 externally-managed-environment 保护。现有环境若有不满足约束的版本，pip 可能调整它及关联依赖；建议使用独立虚拟环境。

模型默认保存到 `~/.local/share/voice-tools/autoqa`。自定义位置需在后续分析与检查中同样指定，不会暗改全局配置：

```bash
voice-tools qa setup --component all --model-dir /path/to/autoqa
voice-tools qa model-doctor --model-dir /path/to/autoqa
voice-tools qa assess data/calls --out outputs/assessment --model-dir /path/to/autoqa \
  --system-channel 1 --channels-verified --ai-start 0
```

最后一条命令的声道与 AI 接管时间必须与实际录音一致，详见[使用说明](automatic-qa.md)。

## 脚本与执行机准备

JSON 或无终端环境必须显式指定 `--component`，缺少时退出 `2`，不等待输入，也不安装。安装进度与 pip 日志在 stderr，stdout 仅包含一个 JSON 结果：

```bash
voice-tools --json qa setup --component all --model-dir /path/to/autoqa \
  > setup-result.json 2> setup-install.log
```

- `summary.components`：各选中组件的完成或失败状态；模型包含 `cached` 与摘要信息，运行库包含实测版本。
- `summary.completed`：所选安装操作是否完成。选择 `all` 时还要求整套推理检查通过。
- `summary.ready`／`summary.check`：整套模型是否可实际推理，以及缺失项。仅安装模型或仅安装运行库可能 `completed=true`、`ready=false`，此时需补齐另一个组件。
- 退出 `0`：所选操作完成（交互取消也为 `0`，不安装）；`2`：参数错误；`3`：安装、下载或必要检查失败。自动部署必须检查 `summary.ready`，不能只看部分安装的退出码。

每个组件独立记录状态；一个失败后仍尝试另一个，已完成组件保留。修复问题后重跑相同命令即可复用满足要求的运行库和已校验模型。pip 最长等待 15 分钟，模型下载子进程 2 分钟，运行库和最终推理检查各 1 分钟；超时记为失败。

`qa setup` 和 `qa model-download` 属于部署准备，不进入离线任务 catalog。任务执行机需预先安装，并在执行端配置 `qa_model_dir`。分析、doctor、任务预检都不会偷偷安装或联网下载。

## 离线与手动准备

兼容原有安装方式：仓库根目录运行 `python -m pip install '.[autoqa]'`，再运行 `voice-tools qa model-download --out /path/to/autoqa`。模型权重固定，运行库按上述版本区间解析，实际版本在 setup 结果中记录。

隔离网环境可在**相同 Python、操作系统与架构**的联网机器下载运行库 wheel；基础 CLI 及其安装包需提前准备：

```bash
python -m pip download --only-binary=:all: --dest wheelhouse \
  'numpy>=1.24,<3' 'onnxruntime>=1.18,<2' 'scipy>=1.10,<2'
voice-tools qa setup --component model --model-dir model-cache
```

将 `wheelhouse/` 与整个 `model-cache/` 复制到执行机后：

```bash
PIP_NO_INDEX=1 PIP_FIND_LINKS="$PWD/wheelhouse" \
  voice-tools qa setup --component all --model-dir "$PWD/model-cache"
```

已正确缓存的模型只做校验，不下载。pip 沿用当前索引、证书和代理配置；安装器不修改这些全局配置。需要完全禁网保证时，应由运行环境同时限制网络。

## 常见问题

| 现象 | 处理 |
|---|---|
| 找不到 CLI／装进了错误环境 | 激活安装 CLI 的虚拟环境，用 `python -m voice_tools qa setup`，核对输出的 Python 路径 |
| No module named pip | 在目标虚拟环境运行 `python -m ensurepip --upgrade` 后重试；发行版环境按其 venv/pip 安装说明准备 |
| externally-managed-environment／权限不足 | 创建用户可写虚拟环境，不使用 `--break-system-packages`；模型路径用可写目录 |
| 找不到兼容 wheel | 使用有对应 CPU wheel 的 Python 与系统架构；安装器不会转为本机编译。基础 CLI 可用不代表可选模型支持该平台 |
| pip 下载、证书或索引失败 | 查看 stderr／安装日志，修复当前 pip 网络配置后重试 `--component runtime`；安装器不改代理或关闭证书校验 |
| 模型下载失败 | 检查 GitHub raw/API 网络访问后重试 `--component model`，或从可信准备机复制已校验的模型目录 |
| 模型摘要不匹配 | 停止使用该文件，人工保留并核对来源；换新目录重新下载。安装器不会覆盖已有损坏／未知模型 |
| 安装成功但 `ready=false` | 查看 `check.issues`；仅安装了一部分时补另一部分，或核对 model-dir；运行库原生加载失败时换受支持环境并重跑 doctor |

本入口只安装整通自动质检依赖。其他工具的部署继续使用各自说明：[音频转换／FFmpeg](manual.md)、[NISQA](nisqa.md)、[ViSQOL](visqol.md)、[双轨延迟引擎](latency-install.md)。
