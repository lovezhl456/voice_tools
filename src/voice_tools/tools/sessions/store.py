from datetime import datetime
from contextlib import contextmanager
import json
import math
import re
from pathlib import Path
import sqlite3
from xml.etree.ElementTree import ParseError as ET_PARSE_ERROR

from voice_tools import __version__
from voice_tools.core.files import new_output, read_json, sha256, write_json
from .pcap import scan


def epoch(value):
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        text = value.replace('Z', '+00:00')
        # Python 3.9 accepts only 3/6-digit fractions; accept normal ISO .5 too.
        text = re.sub(r'(\.\d{1,6})(?=[+-]\d{2}:\d{2}$)', lambda m: m.group(1).ljust(7, '0'), text)
        when = datetime.fromisoformat(text)
        if when.tzinfo is None:
            raise ValueError("时间必须包含时区")
        number = when.timestamp()
    if not math.isfinite(number) or number < 0:
        raise ValueError("时间无效")
    return number


def safe_path(directory, relative):
    if not isinstance(relative,str) or not relative:
        raise ValueError('清单文件路径必须为非空字符串')
    root = Path(directory).resolve()
    path = (root / relative).resolve()
    if path == root or root not in path.parents:
        raise ValueError("清单文件路径越出所在目录")
    return path


@contextmanager
def connect(index):
    path = Path(index)
    if path.is_dir():
        path /= 'sessions.sqlite'
    db = None
    try:
        db = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)
        db.row_factory = sqlite3.Row
        if db.execute('PRAGMA user_version').fetchone()[0] != 1:
            raise ValueError("不支持的会话索引版本")
        yield db
    except sqlite3.Error as error:
        raise ValueError(f"无法读取会话索引：{error}") from error
    finally:
        if db is not None:
            db.close()


def initialize(path):
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript('''PRAGMA user_version=1;
      CREATE TABLE sources(id INTEGER PRIMARY KEY, kind TEXT, path TEXT, host TEXT, sha256 TEXT,
                           start REAL, end REAL, metadata TEXT, status TEXT);
      CREATE TABLE observations(id INTEGER PRIMARY KEY, source_id INTEGER, call_id TEXT NOT NULL, epoch REAL,
                                caller TEXT, callee TEXT, uuid TEXT, data TEXT);
      CREATE INDEX call_lookup ON observations(call_id,epoch);
      CREATE INDEX uuid_lookup ON observations(uuid);
      CREATE INDEX number_lookup ON observations(caller,callee);
      CREATE INDEX source_lookup ON observations(source_id);
      CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT);
    ''')
    return db


def add(db, source, row):
    call_id = row.get('call_id')
    if not isinstance(call_id, str) or not 1 <= len(call_id) <= 1024 or any(ord(c) < 32 for c in call_id):
        raise ValueError("无效的 SIP Call-ID")
    timestamp = epoch(row['epoch']) if row.get('epoch') is not None else None
    db.execute('INSERT INTO observations(source_id,call_id,epoch,caller,callee,uuid,data) VALUES(?,?,?,?,?,?,?)',
               (source, call_id, timestamp, str(row.get('caller') or '')[:256], str(row.get('callee') or '')[:256],
                row.get('uuid'), json.dumps(row, ensure_ascii=False, allow_nan=False)))


def homer_rows(value):
    if not isinstance(value,dict) or value.get('schema_version') not in ('1', '1.0', 1):
        raise ValueError("HOMER JSON 缺少支持的 schema_version")
    rows = value.get('rows', value.get('messages'))
    if not isinstance(rows, list):
        raise ValueError("需要 HOMER search/trace 的 JSON（rows 或 messages）")
    for row in rows:
        if not isinstance(row,dict) or (row.get('_ref') is not None and not isinstance(row['_ref'],dict)):
            raise ValueError('HOMER 行和 _ref 必须为对象')
        ref = row.get('_ref') or {}
        call_id = ref.get('call_id') or row.get('sid') or row.get('callid')
        if not call_id:
            continue
        timestamp = None
        if ref.get('timestamp_us') is not None:
            timestamp = float(ref['timestamp_us']) / 1e6
        elif row.get('timeSeconds') is not None:
            timestamp = float(row['timeSeconds']) + float(row.get('timeUseconds', 0)) / 1e6
        elif isinstance(row.get('create_date'), (int, float)):
            timestamp = row['create_date'] / 1000
        yield {'call_id': call_id, 'epoch': timestamp, 'caller': row.get('from_user', ''),
               'callee': row.get('to_user', ''), 'method': row.get('method'),
               'homer_ref': {**ref, 'id': ref.get('id', row.get('id')), 'dbnode': ref.get('dbnode', row.get('dbnode')),
                             'profile': ref.get('profile', row.get('profile')), 'call_id': call_id},
               'evidence': 'homer_message'}


def build(output, pcaps=(), batches=(), homer_json=(), snapshots=(), sip_ports=(5060,), max_packets=1000000, tshark='tshark'):
    inputs = {}
    warnings = []
    for p in pcaps:
        inputs[str(Path(p).resolve())] = {'kind': 'pcap', 'host': Path(p).parent.name, 'ports': list(sip_ports)}
    for p in homer_json:
        inputs[str(Path(p).resolve())] = {'kind': 'homer', 'host': 'homer'}
    for p in snapshots:
        inputs[str(Path(p).resolve())] = {'kind': 'fs', 'host': Path(p).parent.name}
    for batch in batches:
        root = Path(batch).resolve()
        if (root / 'batch.json').is_file():
            meta = read_json(root / 'batch.json')
            if meta.get('tool') != 'capture-batch' or meta.get('schema_version') != '1.0':
                raise ValueError("批量抓包清单版本不支持")
            manifests = [safe_path(root, h['manifest']) for h in meta.get('hosts', [])]
            if meta.get('status') != 'complete':
                warnings.append(f"批量抓包 {root.name} 状态为 {meta.get('status')}，证据可能不完整。")
        else:
            manifests = [root / 'host.json']
        for manifest in manifests:
            if not manifest.is_file():
                warnings.append(f"缺少主机清单：{manifest}")
                continue
            host = read_json(manifest)
            if host.get('tool') != 'capture-batch-host' or host.get('schema_version') != '1.0':
                raise ValueError("主机抓包清单版本不支持")
            warnings.extend(host.get('warnings', []))
            for f in host.get('files', []):
                path = safe_path(manifest.parent, f['file'])
                inputs[str(path)] = {'kind': 'pcap', 'host': host.get('name', host.get('host')), 'sha256': f['sha256'],
                                     'ports': host.get('sip_ports', list(sip_ports))}
            snapshot_file = manifest.parent / 'sessions.jsonl'
            if snapshot_file.is_file():
                inputs[str(snapshot_file)] = {'kind': 'fs', 'host': host.get('name', host.get('host'))}
    if not inputs:
        raise ValueError("至少提供一份 PCAP、批次、FS 快照或 HOMER JSON")
    output = new_output(output)
    output.chmod(0o700)
    db = initialize(output / 'sessions.sqlite')
    summary = {'schema_version': '1.0', 'tool': 'sessions-index', 'tool_version': __version__, 'sources': [],
               'warnings': warnings, 'errors': 0, 'partial': bool(warnings),
               'notice': '按精确 Call-ID 分组；它不是已确认的跨 B2BUA 业务会话。号码相同不自动合并。'}
    try:
        for name, info in inputs.items():
            source = db.execute('INSERT INTO sources(kind,path,host,status) VALUES(?,?,?,?)',
                                (info['kind'], name, info['host'], 'processing')).lastrowid
            item = {'path': name, 'kind': info['kind'], 'host': info['host']}
            db.execute('SAVEPOINT source_data')
            try:
                digest = sha256(name)
                if info.get('sha256') and digest != info['sha256']:
                    raise ValueError("PCAP SHA-256 与抓包清单不符")
                details = {}
                if info['kind'] == 'pcap':
                    details = scan(name, lambda row: add(db, source, row), info['ports'], max_packets, tshark)
                    summary['partial'] |= details['limited']
                    details['sip_ports'] = info['ports']
                elif info['kind'] == 'homer':
                    if Path(name).stat().st_size > 128 * 1024 * 1024:
                        raise ValueError('HOMER JSON 超过 128 MiB，请缩短查询窗口')
                    data = read_json(name)
                    for row in homer_rows(data):
                        add(db, source, row)
                    details = {'completeness': data.get('completeness', {}), 'warnings': data.get('warnings', [])}
                    summary['partial'] |= details['completeness'].get('status') == 'partial'
                else:
                    with Path(name).open(encoding='utf-8') as stream:
                        for line in stream:
                            row = json.loads(line)
                            if not isinstance(row,dict) or row.get('schema_version') != '1.0':
                                raise ValueError("FS 快照版本无效")
                            add(db, source, {**row, 'epoch': epoch(row['observed_at']), 'evidence': 'fs_snapshot'})
                db.execute('UPDATE sources SET sha256=?,start=?,end=?,metadata=?,status=? WHERE id=?',
                           (digest, details.get('first_epoch'), details.get('last_epoch'), json.dumps(details), 'ok', source))
                db.execute('RELEASE source_data')
                item.update(status='ok', details=details)
            except (ValueError, OSError, ET_PARSE_ERROR, KeyError, TypeError) as error:
                db.execute('ROLLBACK TO source_data')
                db.execute('RELEASE source_data')
                db.execute('UPDATE sources SET status=?,metadata=? WHERE id=?', ('error', json.dumps({'error': str(error)}), source))
                item.update(status='error', error=str(error))
                summary['errors'] += 1
                summary['partial'] = True
            summary['sources'].append(item)
            db.commit()
        summary['sessions'] = db.execute('SELECT count(DISTINCT call_id) FROM observations').fetchone()[0]
        summary['observations'] = db.execute('SELECT count(*) FROM observations').fetchone()[0]
        db.execute('INSERT INTO metadata VALUES(?,?)', ('index_status',json.dumps({
            'partial': summary['partial'], 'errors': summary['errors'], 'warnings': summary['warnings'],
            'notice': summary['notice']})))
        db.commit()
        write_json(output / 'index.json', summary)
    finally:
        db.close()
    return summary


def search(index, call_id=None, number=None, uuid=None, host=None, start=None, end=None, limit=100, offset=0):
    if not 1 <= limit <= 1000 or offset < 0:
        raise ValueError("limit 须为 1–1000，offset 不得为负")
    lower, upper = epoch(start) if start is not None else None, epoch(end) if end is not None else None
    if lower is not None and upper is not None and lower >= upper:
        raise ValueError("检索开始时间须早于结束时间")
    conditions, args = [], []
    for expression, value in [('o.call_id=?', call_id), ('(o.caller=? OR o.callee=?)', number), ('o.uuid=?', uuid), ('s.host=?', host)]:
        if value is not None:
            conditions.append(expression)
            args.extend([value] * expression.count('?'))
    where = ' WHERE ' + ' AND '.join(conditions) if conditions else ''
    having = []
    if lower is not None:
        having.append('max(o.epoch)>=?'); args.append(lower)
    if upper is not None:
        having.append('min(o.epoch)<?'); args.append(upper)
    with connect(index) as db:
        query = ' FROM observations o JOIN sources s ON s.id=o.source_id' + where
        grouped = ('SELECT o.call_id,min(o.epoch) first_epoch,max(o.epoch) last_epoch,count(*) observations,'
                   'group_concat(DISTINCT s.host) hosts,group_concat(DISTINCT o.caller) callers,'
                   'group_concat(DISTINCT o.callee) callees,group_concat(DISTINCT o.uuid) uuids' + query +
                   ' GROUP BY o.call_id' + (' HAVING ' + ' AND '.join(having) if having else ''))
        count = db.execute('SELECT count(*) FROM (' + grouped + ')', args).fetchone()[0]
        rows = db.execute(grouped + ' ORDER BY first_epoch,o.call_id LIMIT ? OFFSET ?', [*args, limit, offset]).fetchall()
        status = json.loads(db.execute("SELECT value FROM metadata WHERE key='index_status'").fetchone()[0])
    return {'total': count, 'returned': len(rows), 'offset': offset, 'sessions': [dict(row) for row in rows],
            'index_status': status, 'partial': status['partial'],
            'notice': '按匹配观测的首末区间与请求时间窗重叠筛选。Call-ID 数不是报文数或已确认业务通话总量。'}


def show(index, call_id):
    with connect(index) as db:
        rows = db.execute('SELECT o.*,s.kind,s.host,s.path,s.sha256,s.start,s.end,s.metadata FROM observations o '
                          'JOIN sources s ON s.id=o.source_id WHERE o.call_id=? ORDER BY o.epoch', (call_id,)).fetchall()
        related = set()
        status = json.loads(db.execute("SELECT value FROM metadata WHERE key='index_status'").fetchone()[0])
        for row in rows:
            data = json.loads(row['data'])
            if not data.get('peer_uuid') or row['epoch'] is None:
                continue
            peers = db.execute('SELECT DISTINCT o.call_id,o.uuid,s.host FROM observations o JOIN sources s ON s.id=o.source_id '
                               'WHERE o.uuid=? AND s.host=? AND o.epoch BETWEEN ? AND ? AND o.call_id<>?',
                               (data['peer_uuid'],row['host'],row['epoch']-60,row['epoch']+60,call_id)).fetchall()
            related.update((p['call_id'],p['uuid'],p['host']) for p in peers)
    if not rows:
        raise ValueError("索引中没有这个精确 Call-ID")
    return {'call_id': call_id, 'index_status': status, 'partial': status['partial'],
            'related_legs': [{'call_id': cid, 'uuid': uuid, 'host': host, 'basis': 'fs_bridge_snapshot'}
                                                for cid,uuid,host in sorted(related)],
            'observations': [{**dict(row), 'data': json.loads(row['data']),
                                               'metadata': json.loads(row['metadata'] or '{}')} for row in rows]}
