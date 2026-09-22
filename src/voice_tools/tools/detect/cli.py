"""Argparse adapter for the detection library; no optional model/runtime required."""
import csv
import json
from pathlib import Path
import uuid

from voice_tools.core.command import emit_result
from voice_tools.core.files import read_json, write_json
from . import store
from .definition import load, fingerprint, read_document


def register(commands):
    parser = commands.add_parser('detect', help='可配置指标、业务标签、跨批次检索与人工复核')
    actions = parser.add_subparsers(dest='action', required=True)
    editor = actions.add_parser('config-ui', help='生成离线图形配置编辑器，可导入、保存浏览器版本和导出 JSON')
    editor.add_argument('--out', type=Path, required=True)
    editor.set_defaults(run=run_editor)
    server = actions.add_parser('serve', help='启动仅本机可访问的配置编辑、版本保存与检测服务')
    server.add_argument('inputs', nargs='+', type=Path, help='启动时明确指定的录音文件或目录')
    server.add_argument('--db', type=Path, required=True)
    server.add_argument('--out', type=Path, required=True, help='本机服务报告目录；可复用同一检测库的服务目录')
    server.add_argument('--port', type=int, default=8766)
    server.set_defaults(run=run_server)
    catalog = actions.add_parser('catalog', help='输出指标、单位、参数默认值与范围')
    catalog.set_defaults(run=run_catalog)
    schema = actions.add_parser('schema', help='输出检测定义 JSON Schema 1.0')
    schema.set_defaults(run=run_schema)
    example = actions.add_parser('config-example', help='导出可修改的示例配置；非已校准业务预置')
    example.add_argument('--kind', choices=('ai-silence', 'low-volume'), default='ai-silence')
    example.add_argument('--out', type=Path, required=True)
    example.set_defaults(run=run_example)
    check = actions.add_parser('config-check', help='严格校验配置，不执行检测')
    check.add_argument('config', type=Path)
    check.set_defaults(run=run_check)
    add = actions.add_parser('config-add', help='保存不可变的定义版本；修改内容必须使用新版本')
    add.add_argument('config', type=Path)
    add.set_defaults(run=run_add)
    listing = actions.add_parser('config-list', help='列出检测库中保存的定义及版本')
    listing.set_defaults(run=run_list)
    export = actions.add_parser('config-export', help='导出已存版本供修改；保存修改时须升级 version')
    export.add_argument('definition', help='id@version')
    export.add_argument('--out', type=Path, required=True)
    export.set_defaults(run=run_export_config)
    run = actions.add_parser('run', help='选择一个或多个配置批量检测 WAV，保留全部历史批次')
    run.add_argument('inputs', nargs='+', type=Path)
    run.add_argument('--config', action='append', type=Path, default=[], help='可重复的配置文件')
    run.add_argument('--definition', action='append', default=[], help='可重复的已存 id@version')
    run.add_argument('--batch', help='新批次标识；默认 UUID')
    run.set_defaults(run=run_detect)
    batches = actions.add_parser('batches', help='列出批次、状态和输入错误')
    batches.set_defaults(run=run_batches)
    query = actions.add_parser('query', help='跨批次查询标签及人工状态；JSON/CSV 导出')
    query.add_argument('--tag', action='append', default=[])
    query.add_argument('--tag-mode', choices=('any', 'all'), default='any')
    query.add_argument('--recording', help='录音摘要前缀或来源路径关键词')
    query.add_argument('--batch')
    query.add_argument('--definition', help='定义 id')
    query.add_argument('--config-version')
    query.add_argument('--status', choices=store.STATUSES)
    query.add_argument('--out', type=Path, help='新的 .json 或 .csv 文件')
    query.set_defaults(run=run_query)
    review = actions.add_parser('review', help='追加人工确认、误报或标签／区间修正')
    review.add_argument('finding_id')
    review.add_argument('--expected-revision', type=int, required=True)
    review.add_argument('--status', choices=store.STATUSES, required=True)
    review.add_argument('--reviewer', required=True)
    review.add_argument('--label')
    review.add_argument('--start', type=float)
    review.add_argument('--end', type=float)
    review.add_argument('--comment', default='')
    review.set_defaults(run=run_review)
    manual = actions.add_parser('manual-add', help='补充漏检样本和原因，不修改自动检测结果')
    for flag in ('batch', 'recording', 'config-hash', 'label', 'reviewer', 'comment'):
        manual.add_argument('--' + flag, required=True)
    manual.add_argument('--start', required=True, type=float)
    manual.add_argument('--end', required=True, type=float)
    manual.set_defaults(run=run_manual)
    history = actions.add_parser('history', help='查看某条结果的完整人工复核历史')
    history.add_argument('finding_id')
    history.set_defaults(run=run_history)
    importing = actions.add_parser('review-import', help='事务导入页面导出的复核；身份或版本冲突时整次拒绝')
    importing.add_argument('review', type=Path)
    importing.set_defaults(run=run_import)
    compare = actions.add_parser('compare', help='对比同一定义两个批次；保留未知和人工证据冲突')
    compare.add_argument('--before', required=True)
    compare.add_argument('--after', required=True)
    compare.add_argument('--definition', required=True)
    compare.add_argument('--out', type=Path)
    compare.set_defaults(run=run_compare)
    report = actions.add_parser('report', help='生成跨批次检索／试听／复核页面快照')
    report.add_argument('--out', type=Path, required=True)
    report.add_argument('--include-audio', action='store_true', help='校验原文件摘要后复制音频及分轨供本地试听')
    report.add_argument('--hide-paths', action='store_true', help='报告仅显示源文件名')
    report.set_defaults(run=run_report)
    for command in (add, listing, export, run, batches, query, review, manual, history, importing, compare, report):
        command.add_argument('--db', type=Path, required=True, help='本地 SQLite 检测库')


def new_file(path):
    if path.exists():
        raise ValueError(f'输出文件已存在，避免覆盖：{path}')
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def emit(args, summary, message, artifacts=None, code=0):
    return emit_result(args, summary, message, artifacts, code)


def run_catalog(args):
    from .metrics import catalog
    return emit(args, catalog(), json.dumps(catalog(), ensure_ascii=False, indent=2))


def run_schema(args):
    from .schema import schema
    result = schema()
    return emit(args, result, json.dumps(result, ensure_ascii=False, indent=2))


def run_example(args):
    config = read_json(Path(__file__).parent / 'resources' / (args.kind + '.json'))
    write_json(new_file(args.out), config)
    return emit(args, {'definition': config['id'] + '@' + config['version']}, f'示例配置已写入 {args.out}；请按业务调整参数', {'config': args.out})


def run_check(args):
    config = load(args.config)
    return emit(args, {'id': config['id'], 'version': config['version'], 'hash': fingerprint(config), 'config': config}, '配置有效；尚未运行检测')


def run_add(args):
    config = load(args.config)
    with store.connect(args.db, create=True) as db:
        digest = store.save_definition(db, config)
    return emit(args, {'id': config['id'], 'version': config['version'], 'hash': digest}, '配置版本已保存', {'database': args.db})


def run_list(args):
    with store.connect(args.db) as db:
        rows = store.definitions(db)
    message = '\n'.join(f"{row['id']}@{row['version']}  {row['name']}" for row in rows) or '没有已保存的定义'
    return emit(args, {'definitions': rows}, message)


def run_export_config(args):
    with store.connect(args.db) as db:
        config = store.select_definition(db, args.definition)
    write_json(new_file(args.out), config)
    return emit(args, {'definition': args.definition}, '配置已导出；修改后请升级 version', {'config': args.out})


def run_detect(args):
    from .service import run
    configs = [load(path) for path in args.config]
    with store.connect(args.db, create=True) as db:
        configs.extend(store.select_definition(db, ref) for ref in args.definition)
        result = run(db, args.inputs, configs, args.batch)
    code = 3 if result['status'] == 'partial' else 1 if result['findings'] else 0
    return emit(args, result, f"批次 {result['batch_id']}：{result['recordings']} 通录音，{result['findings']} 条标签，"
                f"{result['errors']} 项错误，{result['no_windows']} 项无可用窗口", {'database': args.db}, code)


def run_batches(args):
    with store.connect(args.db) as db:
        result = store.batch_details(db)
    return emit(args, {'batches': result}, json.dumps(result, ensure_ascii=False, indent=2))


def export_rows(path, rows):
    if path.suffix.lower() not in ('.json', '.csv'):
        raise ValueError('查询导出文件须为 .json 或 .csv')
    new_file(path)
    if path.suffix.lower() == '.json':
        write_json(path, rows)
        return
    fields = ('id', 'recording_id', 'sources', 'batch_id', 'definition_id', 'version', 'config_hash',
              'origin', 'label', 'start_s', 'end_s', 'status', 'revision', 'reviewer', 'comment', 'reason', 'values', 'automatic')
    with path.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            record = {}
            for key in fields:
                value = row[key]
                if isinstance(value, (dict, list)):
                    value = json.dumps(value, ensure_ascii=False, allow_nan=False)
                if isinstance(value, str) and value.lstrip().startswith(('=', '+', '-', '@', '\t', '\r')):
                    value = "'" + value
                record[key] = value
            writer.writerow(record)


def run_query(args):
    with store.connect(args.db) as db:
        rows = store.query(db, args.tag, args.tag_mode, args.recording, args.batch, args.definition, args.config_version, args.status)
    if args.out:
        export_rows(args.out, rows)
    message = '\n'.join(f"{row['id']}  {row['label']}  {row['start_s']:.2f}–{row['end_s']:.2f}s  {row['status']}  {row['batch_id']}" for row in rows)
    return emit(args, {'count': len(rows), 'findings': rows}, message or '没有符合条件的标签', {'results': args.out} if args.out else None)


def run_review(args):
    decision = {'finding_id': args.finding_id, 'expected_revision': args.expected_revision,
                'status': args.status, 'reviewer': args.reviewer, 'comment': args.comment}
    for flag, key in (('label', 'label'), ('start', 'start_s'), ('end', 'end_s')):
        if getattr(args, flag) is not None:
            decision[key] = getattr(args, flag)
    with store.connect(args.db) as db:
        result = store.review(db, decision)
    return emit(args, result, '人工结论已追加保存')


def run_manual(args):
    item = {'id': str(uuid.uuid4()), 'batch_id': args.batch, 'recording_id': args.recording, 'config_hash': args.config_hash,
            'label': args.label, 'start_s': args.start, 'end_s': args.end, 'reviewer': args.reviewer, 'comment': args.comment}
    with store.connect(args.db) as db:
        result = store.add_manual(db, item)
    return emit(args, result, '漏检样本已保存，自动检测结果保持独立')


def run_history(args):
    with store.connect(args.db) as db:
        result = store.history(db, args.finding_id)
    return emit(args, {'history': result}, json.dumps(result, ensure_ascii=False, indent=2))


def run_import(args):
    packet = read_document(args.review, 16 * 1024 * 1024)
    with store.connect(args.db) as db:
        result = store.import_reviews(db, packet)
    return emit(args, result, f"已保存 {result['reviews']} 条复核、{result['manual']} 条漏检补标；请重新生成报告查看")


def run_compare(args):
    from .service import compare
    with store.connect(args.db) as db:
        result = compare(db, args.before, args.after, args.definition)
    if args.out:
        write_json(new_file(args.out), result)
    return emit(args, result, json.dumps(result, ensure_ascii=False, indent=2), {'comparison': args.out} if args.out else None)


def run_report(args):
    from .report import render
    with store.connect(args.db) as db:
        result = render(db, args.out, args.include_audio, args.hide_paths)
    return emit(args, result, f"复核页面：{args.out / 'index.html'}", {'report': args.out / 'index.html'}, code=3 if result['audio_unavailable'] else 0)


def run_editor(args):
    from .editor import render
    render(new_file(args.out))
    return emit(args, {'editor': str(args.out)}, f'配置编辑界面：{args.out}', {'editor': args.out})


def run_server(args):
    from .server import serve
    if not 0 <= args.port <= 65535:
        raise ValueError('端口必须为 0–65535；0 由系统分配')
    return serve(args)
