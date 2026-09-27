"""Export a single entry point without running analysis or making calls."""
from pathlib import Path

from voice_tools.core.command import emit_result


def register(commands):
    parser = commands.add_parser('workbench', help='生成统一多 Tab HTML 工作台')
    actions = parser.add_subparsers(dest='action', required=True)
    build = actions.add_parser('build', help='汇集本地导出目录，生成可搬移的多 Tab 工作台')
    build.add_argument('--detection', type=Path, help='detect report 导出目录（index.html）')
    build.add_argument('--autoqa', type=Path, help='qa assess 导出目录（report.html）')
    build.add_argument('--gaps', type=Path, help='gaps analyze 导出目录（review.html）')
    build.add_argument('--tasks', type=Path, help='task workbench/review 导出目录；省略则生成任务编排页')
    build.add_argument('--out', type=Path, required=True, help='新的或空的输出目录，打开其中的 index.html')
    build.set_defaults(run=run)


def run(args):
    from .build import build
    summary = build(args.out, {name: getattr(args, name) for name in ('detection', 'autoqa', 'gaps', 'tasks')})
    return emit_result(args, summary, f"多 Tab 工作台：{args.out / 'index.html'}",
                       {'index': args.out / 'index.html', 'manifest': args.out / 'workbench.json'})
