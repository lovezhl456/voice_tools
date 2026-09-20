"""Public task interfaces; runtime images and browsers use the same contracts."""
from pathlib import Path


def tail(path, size=4000):
    if not path.is_file(): return ''
    with path.open('rb') as stream:
        stream.seek(max(0, path.stat().st_size - size))
        return stream.read(size).decode('utf-8', errors='replace')


def register(commands):
    parser = commands.add_parser('task', help='跨主机任务包、执行回执与可视化复查')
    actions = parser.add_subparsers(dest='action', required=True)
    cat = actions.add_parser('catalog', help='输出可编排工具与参数合同'); cat.set_defaults(run=run)
    pack = actions.add_parser('pack', help='收集明确引用的素材，生成 ZIP64 任务包')
    pack.add_argument('task_file', type=Path); pack.add_argument('--root', required=True, type=Path); pack.add_argument('--out', required=True, type=Path)
    pack.set_defaults(run=run)
    for action in ('check', 'run'):
        cmd = actions.add_parser(action, help='离线检查环境与任务' if action == 'check' else '执行任务；只调用已注册工具')
        cmd.add_argument('package', type=Path); cmd.add_argument('--profile', type=Path)
        if action == 'run': cmd.add_argument('--out', type=Path, required=True)
        cmd.set_defaults(run=run)
    for action in ('status', 'logs'):
        cmd = actions.add_parser(action, help='查看已落盘的运行记录')
        cmd.add_argument('run_dir', type=Path); cmd.set_defaults(run=run)
    collect = actions.add_parser('collect', help='带回完整输入、实际产物与缺失状态')
    collect.add_argument('run_dir', type=Path); collect.add_argument('--out', required=True, type=Path); collect.set_defaults(run=run)
    review = actions.add_parser('review', help='校验结果包，生成离线可视化复查页')
    review.add_argument('package', type=Path); review.add_argument('--out', required=True, type=Path); review.set_defaults(run=run)
    web = actions.add_parser('workbench', help='导出静态任务编排工作台和最新命令合同')
    web.add_argument('--out', required=True, type=Path); web.set_defaults(run=run)


def run(args):
    from voice_tools.core.command import emit_result
    from voice_tools.core.files import read_json
    from . import bundle, runner
    code = 0
    if args.action == 'catalog':
        from .catalog import catalog
        result = {'schema_version': '1.0', 'commands': catalog()}
    elif args.action == 'pack': result = bundle.pack(args.task_file, args.root, args.out)
    elif args.action == 'check':
        result = runner.check(args.package, args.profile); code = 0 if result['ready'] else 2
    elif args.action == 'run':
        result = runner.run(args.package, args.out, args.profile)
        code = 0 if result['status'] in ('completed', 'findings') else 3
    elif args.action == 'collect': result = runner.collect(args.run_dir, args.out)
    elif args.action in ('status', 'logs'):
        result = read_json(args.run_dir / 'run.json')
        if args.action == 'logs':
            result = {'run_id': result['run_id'], 'steps': [{'id': s['id'], 'status': s['status'],
                'stderr_tail': tail(args.run_dir / 'steps' / s['id'] / 'stderr.log')} for s in result['steps']]}
    elif args.action == 'review':
        from .review import review
        result = review(args.package, args.out)
    else:
        from .review import workbench
        result = workbench(args.out)
    return emit_result(args, result, str(result), exit_code=code)
