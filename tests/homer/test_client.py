"""HTTP integration and regression tests; no live HOMER instance required."""
import contextlib
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import ssl
import stat
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

ROOT = Path(__file__).resolve().parent
from voice_tools.tools.homer import client as cli

T0 = 1789459200000  # 2026-09-15T08:00:00Z
MAPPING = json.loads((ROOT / 'fixtures/mapping.json').read_text())
PCAP = struct.pack('<IHHIIII', 0xa1b2c3d4, 2, 4, 0, 0, 65536, 1)
SECRET = 'test-secret-do-not-print'


def message(ident=1, offset=100, node='db-a', caller='1001', method='INVITE', capture=101):
    raw = ('INVITE sip:1002@example.net SIP/2.0\r\nCall-ID: call-a@example.net\r\n'
           'CSeq: 1 INVITE\r\nFrom: <sip:1001@example.net>;tag=a\r\nTo: <sip:1002@example.net>\r\n'
           'Content-Length: 0\r\n\r\n')
    return dict(id=ident, sid='call-a@example.net', callid='call-a@example.net', dbnode=node, node=node,
                table='hep_proto_1_call', profile='1_call', create_date=T0 + offset,
                timeSeconds=(T0+offset)//1000, timeUseconds=((T0+offset)%1000)*1000,
                srcIp='192.0.2.10', dstIp='192.0.2.20', srcPort=5060, dstPort=5080,
                protocol=17, captureId=capture, capturePass=SECRET,
                from_user=caller, to_user='1002', method=method, raw=raw)


class FakeHomer(ThreadingHTTPServer):
    daemon_threads = True
    def __init__(self):
        super().__init__(('127.0.0.1', 0), Handler)
        self.reset()

    def reset(self):
        self.requests, self.failures = [], []
        self.rows = [message(i+1, i*2000+100) for i in range(6)]
        self.mapping = copy.deepcopy(MAPPING)
        self.forced = None
        self.delay = 0
        self.fail_search_after = None
        self.search_count = 0
        self.export_pcap = PCAP
        self.export_text = b'INVITE sip:test@example.net SIP/2.0\r\n\r\n'
        self.truncate_body = False


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, code, data, content_type='application/json', headers=None):
        raw = data if isinstance(data, bytes) else json.dumps(data).encode()
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(raw) + (100 if self.server.truncate_body else 0)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        try:
            self.wfile.write(raw)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        self.dispatch()

    def do_POST(self):
        self.dispatch()

    def dispatch(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length']))) if self.command == 'POST' else None
        self.server.requests.append((self.command, self.path, body, dict(self.headers)))
        if self.server.delay:
            time.sleep(self.server.delay)
        if self.server.forced:
            self.reply(*self.server.forced)
            return
        try:
            assert self.path.startswith('/homer/api/v3/'), 'Lost reverse proxy prefix'
            route = self.path[len('/homer/api/v3'):]
            if route == '/auth':
                assert body == {'username': 'operator', 'password': SECRET}
                self.reply(201, {'token': SECRET, 'scope': 'fixture-user', 'user': {'admin': False}})
                return
            if self.headers.get('Authorization') != 'Bearer ' + SECRET and self.headers.get('Auth-Token') != SECRET:
                self.reply(401, {'message': 'denied'})
                return
            if self.command == 'GET':
                assert route == '/mapping/protocol', 'Unexpected GET route'
                self.reply(200, self.server.mapping)
                return
            p = body['param']
            assert body['timestamp']['from'] % 1000 == 0 and body['timestamp']['to'] % 1000 == 0
            assert p['timezone'] == {'value': 0, 'name': 'UTC'}
            assert isinstance(p['location']['node'], list)
            assert list(p['search']) == ['1_call']
            search = p['search']['1_call']
            if route == '/search/call/data':
                self.server.search_count += 1
                if self.server.fail_search_after and self.server.search_count > self.server.fail_search_after:
                    self.reply(503, {'message': 'simulated node/service failure'})
                    return
                assert isinstance(search, list), 'Search requires condition array'
                assert all(isinstance(f['value'], str) for f in search), 'HOMER silently skips numeric JSON values'
                assert search[-1] == {'name': 'limit', 'type': 'integer', 'value': str(p['limit'])}, 'Limit must be last and inside conditions'
                types = {f['id']: f['type'] for f in MAPPING['data'][0]['fields_mapping']}
                types.update(id='integer', sid='string', raw='string')
                for term in search[:-1]:
                    assert term['type'] == types[term['name']], 'Use live mapping type'
                rows = self.filtered(body, search[:-1])
                per_node = {}
                # LIMIT precedes sorting in HOMER. Intentionally return an arbitrary selection.
                for row in reversed(rows):
                    per_node.setdefault(row['dbnode'], [])
                    if len(per_node[row['dbnode']]) < p['limit']:
                        per_node[row['dbnode']].append(row)
                rows = [dict(r) for group in per_node.values() for r in group]
                for row in rows:
                    row.pop('raw', None)
                    row.pop('profile', None)
                self.reply(201, {'total': len(rows), 'data': rows, 'keys': []})
            elif route == '/search/call/message':
                assert isinstance(search, dict) and isinstance(search['id'], int)
                assert p['limit'] == 10
                rows = [r for r in self.filtered(body, []) if r['id'] == search['id']]
                rows = [{k: v for k, v in r.items() if k not in ('node', 'dbnode', 'profile', 'table')} for r in rows]
                self.reply(201, {'data': rows, 'total': len(rows), 'keys': []})
            else:
                assert search == {'id': 0, 'callid': getattr(self.server, 'expected_call_ids', ['call-a@example.net']), 'uuid': []}, 'Transaction is an object, not search array'
                if route == '/call/transaction':
                    rows = [r for r in self.filtered(body, []) if r['sid'] in search['callid']]
                    self.reply(201, {'total': len(rows), 'data': {'messages': rows, 'calldata': [], 'hosts': {}, 'alias': {}}, 'keys': []})
                elif route == '/export/call/messages/pcap':
                    self.reply(200, self.server.export_pcap, 'application/octet-stream')
                elif route == '/export/call/messages/text':
                    self.reply(200, self.server.export_text, 'text/plain')
                else:
                    raise AssertionError('Unexpected route')
        except Exception as exc:
            self.server.failures.append(str(exc))
            self.reply(400, {'message': str(exc)})

    def filtered(self, body, conditions):
        p, interval = body['param'], body['timestamp']
        rows = []
        for row in self.server.rows:
            # HOMER truncates incoming milliseconds, uses inclusive SQL BETWEEN.
            if not interval['from'] <= row['create_date'] <= interval['to']:
                continue
            if p['location']['node'] and row['dbnode'] not in p['location']['node']:
                continue
            matches = []
            for term in conditions:
                actual = str(row.get(term['name'].split('.')[-1], ''))
                value = term['value']
                negated = value.startswith('!=')
                if negated:
                    value = value[2:]
                if '%' in value or term['name'] == 'raw':
                    pattern = re.escape(value).replace('%', '.*').replace('_', '.')
                    matched = bool(re.fullmatch(pattern, actual, flags=re.S | (re.I if term['name'] == 'raw' else 0)))
                else:
                    matched = actual in value.split(';')
                matches.append(not matched if negated else matched)
            if not matches or (any(matches) if p.get('orlogic') else all(matches)):
                rows.append(copy.deepcopy(row))
        return rows


class CliIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = FakeHomer()
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        self.server.reset()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.url = 'http://127.0.0.1:%d/homer' % self.server.server_port
        self.config = str(Path(self.tmp.name) / 'config.json')
        self.env = {k: v for k, v in os.environ.items() if not k.startswith('HOMER_')}
        self.env.update(HOMER_URL=self.url, HOMER_CONFIG=self.config, HOMER_TOKEN=SECRET, NO_PROXY='127.0.0.1,localhost')
        self.interval = ['--from', str(T0), '--to', str(T0+12000)]

    def tearDown(self):
        self.assertEqual(self.server.failures, [], 'Mock rejected the actual CLI HTTP contract')

    def run_cli(self, *argv, code=0, env=None):
        cp = subprocess.run([sys.executable, '-m', 'voice_tools', 'homer', *argv], capture_output=True, text=True,
                            env=env or self.env, timeout=10)
        self.assertEqual(cp.returncode, code, cp.stdout + cp.stderr)
        self.assertNotIn(SECRET, cp.stdout + cp.stderr)
        if code in (0, 6):
            self.assertEqual(cp.stderr, '')
            return json.loads(cp.stdout)
        self.assertEqual(cp.stdout, '')
        data = json.loads(cp.stderr)
        self.assertEqual(data['error']['exit_code'], code)
        return data

    def test_init_login_cached_doctor_logout(self):
        env = dict(self.env)
        env.pop('HOMER_TOKEN')
        env.update(HOMER_USERNAME='operator', HOMER_PASSWORD=SECRET)
        self.run_cli('init', env=env)
        config = json.loads(Path(self.config).read_text())
        self.assertNotIn('password', config)
        self.assertEqual(config['url'], self.url + '/api/v3')
        self.run_cli('login', env=env)
        saved = list(Path(self.tmp.name).glob('token-*.json'))
        self.assertEqual(len(saved), 1)
        self.assertEqual(stat.S_IMODE(saved[0].stat().st_mode), 0o600)
        env.pop('HOMER_PASSWORD')
        env.pop('HOMER_USERNAME')
        data = self.run_cli('doctor', env=env)
        self.assertEqual(data['profiles'], ['1_call'])
        self.run_cli('logout', env=env)
        self.assertFalse(saved[0].exists())
        self.run_cli('doctor', env=env, code=3)

    def test_environment_password_login_without_cache(self):
        env = dict(self.env)
        env.pop('HOMER_TOKEN')
        env.update(HOMER_USERNAME='operator', HOMER_PASSWORD=SECRET)
        self.run_cli('doctor', env=env)
        self.assertFalse(list(Path(self.tmp.name).glob('token-*')))

    def test_api_token_auth(self):
        env = dict(self.env)
        env.pop('HOMER_TOKEN')
        env['HOMER_AUTH_TOKEN'] = SECRET
        self.run_cli('fields', '--profile', '1_call', env=env)
        headers = self.server.requests[-1][3]
        self.assertNotIn('Authorization', headers)
        self.assertEqual(headers['Auth-Token'], SECRET)

    def test_conflicting_credentials(self):
        env = dict(self.env, HOMER_AUTH_TOKEN=SECRET)
        self.run_cli('doctor', code=2, env=env)
        self.assertEqual(self.server.requests, [])

    def test_global_options_before_and_after_subcommand(self):
        env = dict(self.env, HOMER_URL=self.url + '/wrong-prefix')
        for args in (('--url', self.url, 'doctor'), ('doctor', '--url', self.url)):
            with self.subTest(args=args):
                self.server.requests.clear()
                self.run_cli(*args, env=env)
                self.assertTrue(self.server.requests)
                self.assertTrue(all(path.startswith('/homer/api/v3/')
                                    for _, path, _, _ in self.server.requests))

    def test_mapping_string_and_malformed_field(self):
        fields = self.server.mapping['data'][0]['fields_mapping']
        fields.append({'id': 123, 'type': 'string'})
        self.server.mapping['data'][0]['fields_mapping'] = json.dumps(fields)
        data = self.run_cli('fields')
        self.assertEqual(data['profiles']['1_call']['protocol_header.captureId'], 'integer')
        self.assertNotIn('protocol_header.capturePass', data['profiles']['1_call'])

    def test_multi_dimension_and_filters(self):
        data = self.run_cli('search', *self.interval, '--caller', '1001', '--callee', '1002', '--src-ip', '192.0.2.10',
                            '--dst-port', '5080', '--capture-id', '101', '--transport', 'udp', '--method', 'INVITE', '--node', 'db-a')
        self.assertEqual(data['count'], 6)
        self.assertEqual(data['completeness']['status'], 'no_limit_detected')
        body = self.server.requests[-1][2]
        self.assertFalse(body['param']['orlogic'])
        self.assertEqual(data['rows'][0]['_ref']['profile'], '1_call')
        self.assertNotIn('capturePass', data['rows'][0])

    def test_or_conditions_and_response_status(self):
        self.server.rows = [message(1, method='503'), message(2, method='INVITE'), message(3, method='BYE')]
        data = self.run_cli('search', *self.interval, '--status', '503', '--method', 'INVITE', '--logic', 'or')
        self.assertEqual({r['method'] for r in data['rows']}, {'503', 'INVITE'})

    def test_semicolon_list_wildcard_and_string_exclusion(self):
        self.server.rows += [message(99, caller='2001')]
        data = self.run_cli('search', *self.interval, '--caller', '1001;2001')
        self.assertEqual(data['count'], 7)
        data = self.run_cli('search', *self.interval, '--caller', '100%', '--exclude', 'data_header.from_user=2001')
        self.assertEqual(data['count'], 6)

    def test_raw_contains_is_server_query(self):
        data = self.run_cli('search', *self.interval, '--raw-contains', 'cseq: 1 invite')
        self.assertEqual(data['count'], 6)
        self.assertNotIn('raw', data['rows'][0])

    def test_unknown_profile_field_and_ambiguous_values_rejected(self):
        cases = [('--profile', '1_missing'), ('--where', 'users.password=x'),
                 ('--where', 'smartinput=sid=x'), ('--where', 'protocol_header.dstPort=5060;5080'),
                 ('--exclude', 'protocol_header.dstPort=5080'), ('--caller', '100%;200%'),
                 ('--caller', 'a&b'), ('--caller', '!=1001'), ('--caller', '1001,1002')]
        for args in cases:
            with self.subTest(args=args):
                before = self.server.search_count
                self.run_cli('search', *self.interval, *args, code=2)
                self.assertEqual(self.server.search_count, before)

    def test_dry_run_does_not_execute_search(self):
        data = self.run_cli('search', *self.interval, '--caller', '1001', '--dry-run')
        self.assertEqual(self.server.search_count, 0)
        self.assertEqual(data['body']['param']['search']['1_call'][-1]['value'], '200')

    def test_single_query_limit_is_partial(self):
        data = self.run_cli('search', *self.interval, '--limit', '2', code=6)
        self.assertEqual(data['count'], 2)
        self.assertEqual(data['completeness']['status'], 'partial')

    def test_all_recovers_unordered_limited_rows(self):
        data = self.run_cli('search', *self.interval, '--limit', '2', '--all')
        self.assertEqual({r['id'] for r in data['rows']}, set(range(1, 7)))
        self.assertGreater(data['search_requests'], 1)
        self.assertEqual(data['completeness']['status'], 'no_limit_detected')

    def test_multinode_id_collisions_survive_dedup(self):
        self.server.rows += [message(1, 100, node='db-b')]
        data = self.run_cli('search', *self.interval, '--limit', '2', '--all')
        self.assertEqual(data['count'], 7)
        self.assertEqual(len([r for r in data['rows'] if r['id'] == 1]), 2)

    def test_multinode_combined_total_is_not_per_node_limit(self):
        self.server.rows = [message(1, node='db-a'), message(1, node='db-b')]
        data = self.run_cli('search', *self.interval, '--limit', '2')
        self.assertEqual(data['completeness']['status'], 'no_limit_detected')

    def test_dense_second_is_explicitly_incomplete(self):
        self.server.rows = [message(i, i) for i in range(1, 5)]
        data = self.run_cli('search', *self.interval, '--limit', '2', '--all', code=6)
        self.assertTrue(any(w['reason'] == 'one_second_limit' for w in data['completeness']['unresolved_windows']))

    def test_budget_keeps_partial_results(self):
        data = self.run_cli('search', *self.interval, '--limit', '2', '--all', '--max-requests', '1', code=6)
        self.assertEqual(data['search_requests'], 1)
        self.assertEqual(data['count'], 2)
        self.assertTrue(data['completeness']['unresolved_windows'])

    def test_later_http_failure_preserves_rows(self):
        self.server.fail_search_after = 1
        data = self.run_cli('search', *self.interval, '--limit', '2', '--all', code=6)
        self.assertEqual(data['count'], 2)
        self.assertEqual(data['completeness']['status'], 'partial')

    def test_millisecond_interval_and_exclusive_end(self):
        self.server.rows = [message(1, 100), message(2, 500), message(3, 1000), message(4, 1500)]
        data = self.run_cli('search', '--from', str(T0+500), '--to', str(T0+1500))
        self.assertEqual([r['id'] for r in data['rows']], [2, 3])
        self.assertEqual(self.server.requests[-1][2]['timestamp'], {'from': T0, 'to': T0+2000})

    def test_whole_second_overlap_deduplicated(self):
        self.server.rows = [message(i, i*1000) for i in range(1, 10)]
        data = self.run_cli('search', *self.interval, '--limit', '3', '--all')
        self.assertEqual(data['count'], 9)

    def test_empty_query_result_is_valid(self):
        data = self.run_cli('search', *self.interval, '--caller', 'none')
        self.assertEqual(data['count'], 0)
        self.assertEqual(data['completeness']['capture_complete'], 'unknown')

    def test_trace_raw_is_opt_in_and_schema_is_transaction(self):
        data = self.run_cli('trace', *self.interval, '--call-id', 'call-a@example.net')
        self.assertEqual(data['count'], 6)
        self.assertNotIn('raw', data['messages'][0])
        self.assertEqual(data['completeness']['status'], 'unknown')
        data = self.run_cli('trace', *self.interval, '--call-id', 'call-a@example.net', '--include-raw')
        self.assertTrue(data['messages'][0]['raw'].startswith('INVITE'))

    def test_message_requires_node_and_returns_reference(self):
        self.run_cli('message', *self.interval, '--id', '1', code=2)
        data = self.run_cli('message', *self.interval, '--id', '1', '--node', 'db-a')
        self.assertEqual(data['count'], 1)
        self.assertEqual(data['messages'][0]['_ref']['dbnode'], 'db-a')

    def test_exports_pcap_text_json_and_no_clobber(self):
        for format_ in ('pcap', 'text', 'json'):
            path = Path(self.tmp.name) / ('call.'+format_)
            with self.subTest(format=format_):
                data = self.run_cli('export', *self.interval, '--call-id', 'call-a@example.net', '--format', format_, '--output', str(path))
                self.assertEqual(path.stat().st_size, data['bytes'])
                if format_ == 'pcap':
                    self.assertEqual(path.read_bytes(), PCAP)
                if format_ == 'json':
                    self.assertIn('raw', json.loads(path.read_text())['messages'][0])
                old = path.read_bytes()
                self.run_cli('export', *self.interval, '--call-id', 'call-a@example.net', '--format', format_, '--output', str(path), code=7)
                self.assertEqual(path.read_bytes(), old)

    def test_bad_pcap_and_json_text_exports_rejected(self):
        self.server.export_pcap = b'{"error":"bad"}'
        path = str(Path(self.tmp.name)/'bad.pcap')
        self.run_cli('export', *self.interval, '--call-id', 'call-a@example.net', '--output', path, code=5)
        self.assertFalse(Path(path).exists())
        self.server.export_text = b'{"message":"error"}'
        self.run_cli('export', *self.interval, '--call-id', 'call-a@example.net', '--format', 'text', '--output', path, code=5)

    def test_offline_analysis_from_export(self):
        path = str(Path(self.tmp.name)/'trace.json')
        self.run_cli('export', *self.interval, '--call-id', 'call-a@example.net', '--format', 'json', '--output', path)
        before = len(self.server.requests)
        data = self.run_cli('analyze', '--input', path)
        self.assertEqual(data['fragmentation']['verdict'], 'unknown')
        self.assertEqual(data['raw_messages_available'], 6)
        self.assertEqual(len(self.server.requests), before)

    def test_http_error_json_and_no_secret_echo(self):
        for status, exitcode in ((401, 3), (403, 3), (404, 4), (500, 4), (503, 4)):
            with self.subTest(status=status):
                self.server.forced = (status, {'message': SECRET})
                self.run_cli('doctor', code=exitcode)

    def test_html_and_wrong_json_shape_rejected(self):
        for body, content_type in ((b'<html>Login</html>', 'text/html'), ({'data': {}}, 'application/json')):
            self.server.forced = (200, body, content_type)
            self.run_cli('doctor', code=5)

    def test_redirect_not_followed(self):
        self.server.forced = (302, b'', 'text/plain', {'Location': self.url + '/other'})
        self.run_cli('doctor', code=4)
        self.assertEqual(len(self.server.requests), 1)

    def test_response_size_limit(self):
        self.server.forced = (200, b' ' * (1024 * 1024 + 1), 'application/json')
        self.run_cli('doctor', '--max-response-mib', '1', code=5)

    def test_nonfinite_json_rejected(self):
        self.server.forced = (200, b'{"data":[], "count":NaN}', 'application/json')
        self.run_cli('doctor', code=5)

    def test_zero_timeout_and_response_budget_rejected(self):
        self.run_cli('doctor', '--timeout', '0', code=2)
        self.run_cli('doctor', '--max-response-mib', '0', code=2)
        self.assertEqual(self.server.requests, [])

    def test_invalid_config_types_report_json_error(self):
        Path(self.config).write_text('{"username":123}')
        self.run_cli('doctor', code=2)

    def test_truncated_http_response_is_structured_error(self):
        self.server.truncate_body = True
        self.run_cli('doctor', code=4)

    def test_timeout(self):
        self.server.delay = 0.25
        self.run_cli('doctor', '--timeout', '0.02', code=4)
        time.sleep(0.3)  # allow the deliberately delayed mock handler to finish before resetting it

    def test_time_validation_happens_before_network(self):
        for args in (['--from', '2026-09-15T08:00:00', '--to', '2026-09-15T09:00:00'],
                     ['--since', '0m'], ['--since', '1h', '--from', str(T0)], ['--from', str(T0)]):
            self.run_cli('search', *args, code=2)
        self.assertEqual(self.server.requests, [])

    def test_schema_is_offline_and_json_errors(self):
        data = self.run_cli('schema')
        self.assertIn('search', data['commands'])
        self.run_cli('search', '--bogus', code=2)
        self.assertEqual(self.server.requests, [])


class LocalRegression(unittest.TestCase):
    def test_timezone_conversion(self):
        self.assertEqual(cli.parse_time('2026-09-15T16:00:00+08:00'), T0)
        self.assertEqual(cli.parse_time('2026-09-15T08:00:00Z'), T0)

    def test_url_prefix_and_credentials(self):
        self.assertEqual(cli.api_url('https://homer.example.net/ops/'), 'https://homer.example.net/ops/api/v3')
        self.assertEqual(cli.api_url('https://homer.example.net/api/v3'), 'https://homer.example.net/api/v3')
        for bad in ('https://user:pass@host', 'https://host/?q=token', 'file:///etc/passwd'):
            with self.assertRaises(cli.CliError):
                cli.api_url(bad)

    def test_source_route_allowlist_blocks_mapping_reset_and_import(self):
        client = object.__new__(cli.Client)
        for method, path in [('GET', '/mapping/protocol/reset'), ('POST', '/import/data/pcap'), ('DELETE', '/user/1')]:
            with self.assertRaises(cli.CliError):
                client.request(method, path)

    def test_tls_verification_enabled(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {'HOMER_URL': 'https://example.net', 'HOMER_CONFIG': str(Path(tmp)/'config.json')}):
            client = cli.Client(cli.build_parser().parse_args(['doctor']))
            handler = next(h for h in client.opener.handlers if hasattr(h, '_context'))
            self.assertTrue(handler._context.check_hostname)
            self.assertEqual(handler._context.verify_mode, ssl.CERT_REQUIRED)

    def test_oversize_is_only_risk_and_tcp_excluded(self):
        udp, tcp = message(), message(2)
        udp['raw'] += 'x'*1600
        tcp.update(raw=udp['raw'], protocol=6)
        data = cli.analyze([udp, tcp], 1500, {})
        self.assertEqual(data['fragmentation']['verdict'], 'unknown')
        self.assertEqual(len([f for f in data['findings'] if f['type'] == 'udp_size_risk']), 1)

    def test_cross_sensor_duplicates_not_classified_as_retransmission(self):
        rows = [message(1, capture=101), message(2, capture=102), message(3, node='db-b')]
        data = cli.analyze(rows, 1500, {})
        self.assertFalse(any(f['type'] == 'repeated_udp_payload' for f in data['findings']))
        rows.append(message(4, offset=1000))
        data = cli.analyze(rows, 1500, {})
        repeated = [f for f in data['findings'] if f['type'] == 'repeated_udp_payload']
        self.assertEqual(len(repeated), 1)
        self.assertEqual(repeated[0]['observations'], 2)

    def test_content_length_counts_utf8_bytes(self):
        row = message()
        row['raw'] = 'SIP/2.0 200 OK\r\nContent-Length: 3\r\n\r\n中'
        self.assertFalse(cli.analyze([row], 1500, {})['findings'])
        row['raw'] = row['raw'].replace('Length: 3', 'Length: 1')
        data = cli.analyze([row], 1500, {})
        self.assertEqual(data['findings'][0]['type'], 'content_length_mismatch')

    def test_analysis_without_raw_does_not_claim_clean_capture(self):
        data = cli.analyze([{'id': 1}], 1500, {})
        self.assertEqual(data['raw_messages_available'], 0)
        self.assertEqual(data['fragmentation']['verdict'], 'unknown')
        self.assertTrue(data['warnings'])

    def test_token_cache_scoped_by_url_and_user(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {'HOMER_CONFIG': str(Path(tmp)/'config.json')}):
            parser = cli.build_parser()
            a = cli.Client(parser.parse_args(['doctor', '--url', 'https://one.example', '--username', 'alice']))
            b = cli.Client(parser.parse_args(['doctor', '--url', 'https://two.example', '--username', 'alice']))
            c = cli.Client(parser.parse_args(['doctor', '--url', 'https://one.example', '--username', 'bob']))
            self.assertEqual(len({a.token_file, b.token_file, c.token_file}), 3)

    def test_atomic_output_does_not_follow_or_replace_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, target = Path(tmp)/'link', Path(tmp)/'target'
            target.write_text('keep')
            path.symlink_to(target)
            with self.assertRaises(cli.CliError):
                cli.write_file(path, b'new')
            self.assertEqual(target.read_text(), 'keep')
            cli.write_file(path, b'new', force=True)
            self.assertEqual(target.read_text(), 'keep')
            self.assertEqual(path.read_text(), 'new')


if __name__ == '__main__':
    unittest.main()
