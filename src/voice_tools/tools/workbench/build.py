"""Copy explicit local exports intact and publish a portable HTML shell."""
import html
import json
import os
from pathlib import Path
import shutil
import tempfile

from voice_tools import __version__
from voice_tools.core.files import write_json

WEB = Path(__file__).parent / 'web'
TABS = (
    ('detection', '检测与复核', 'index.html', '配置检测规则、试听命中片段、补充漏检与标签。'),
    ('autoqa', '整通质检', 'report.html', '查看整通录音质检报告与人工例外复核。'),
    ('gaps', '输出间隙', 'review.html', '定位长停顿与短断音，试听并标注候选间隙。'),
    ('tasks', '跨主机任务', 'index.html', '编排任务、导出配置，将执行结果带回复查。'),
)


def validate_inputs(output, inputs):
    """Reject invalid/recursive copies before creating any output."""
    if output.is_symlink():
        raise ValueError('输出不能是符号链接')
    destination = output.resolve()
    if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
        raise ValueError('输出目录必须不存在或为空，避免覆盖已有文件')
    sources = {}
    for key, _, entry, _ in TABS:
        supplied = inputs.get(key)
        if supplied is None:
            continue
        supplied = Path(supplied)
        if supplied.is_symlink() or not supplied.is_dir():
            raise ValueError(f'--{key} 必须是本地导出目录，不能是符号链接')
        source = supplied.resolve()
        if source == destination or source in destination.parents or destination in source.parents:
            raise ValueError(f'--{key} 输入与输出目录不能互相包含')
        if not (source / entry).is_file():
            raise ValueError(f'--{key} 缺少入口文件 {entry}')
        for path in source.rglob('*'):
            if path.is_symlink() or not (path.is_file() or path.is_dir()):
                raise ValueError(f'--{key} 包含符号链接或特殊文件：{path.relative_to(source)}')
        sources[key] = source
    return destination, sources


def empty_page(path, key, label):
    path.parent.mkdir()
    command = f'voice-tools workbench build --{key} /path/to/export --out outputs/workbench-new'
    path.write_text('<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
                    '<meta name="viewport" content="width=device-width,initial-scale=1">'
                    '<link rel="icon" href="data:,"><link rel="stylesheet" href="../style.css">'
                    f'<title>{html.escape(label)} · 尚未导入</title><body class="document">'
                    '<main><p class="eyebrow">尚未导入</p>'
                    f'<h1>{html.escape(label)}</h1><p>本次导出未提供这个页面的数据。</p>'
                    '<p>生成对应工具的报告后，用下面的命令汇入新的工作台：</p>'
                    f'<pre>{html.escape(command)}</pre>'
                    '<p>已有报告和人工复核数据不会被修改。</p></main></body></html>', encoding='utf-8')


def build(output, inputs):
    destination, sources = validate_inputs(Path(output), inputs)
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Build off to the side: a failed copy/export must not leave a successful-looking index.
    with tempfile.TemporaryDirectory(prefix='.voice-workbench-', dir=destination.parent) as temporary:
        staged = Path(temporary) / 'site'
        staged.mkdir()
        tabs = []
        for key, label, entry, description in TABS:
            if key in sources:
                shutil.copytree(sources[key], staged / key)
                status = 'imported'
            elif key == 'tasks':
                from voice_tools.tools.task.review import workbench
                workbench(staged / key)
                status = 'generated'
            else:
                empty_page(staged / key / entry, key, label)
                status = 'empty'
            tabs.append({'id': key, 'label': label, 'url': f'{key}/{entry}',
                         'description': description, 'status': status})
        tabs.append({'id': 'guide', 'label': '使用说明', 'url': 'guide.html',
                     'description': '统一导出、离线打开和数据保存说明。', 'status': 'generated'})
        for asset in ('app.js', 'style.css', 'guide.html'):
            shutil.copyfile(WEB / asset, staged / asset)
        data = json.dumps(tabs, ensure_ascii=False).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
        template = (WEB / 'index.html').read_text(encoding='utf-8')
        (staged / 'index.html').write_text(template.replace('__TABS__', data), encoding='utf-8')
        manifest = {'schema_version': '1.0', 'tool_version': __version__, 'tabs': tabs, 'network_accessed': False}
        write_json(staged / 'workbench.json', manifest)
        # Recheck after generation; do not overwrite a directory changed by another process.
        if destination.exists():
            destination.rmdir()  # Fails if no longer empty.
        os.replace(staged, destination)
    return manifest
