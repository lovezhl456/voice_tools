import ast
import contextlib
import io
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from voice_tools.cli import main
from voice_tools.tools.recording_qa import setup
from voice_tools.tools.task.catalog import catalog


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name) / 'model with spaces'
        self.stderr = io.StringIO()
        # Keep mocked installation progress out of the test runner output.
        self.redirect = contextlib.redirect_stderr(self.stderr)
        self.redirect.__enter__()
        self.addCleanup(self.redirect.__exit__, None, None, None)

    def test_menu_retries_invalid_input_and_selects_without_default_install(self):
        for answer, expected in [('1', 'all'), ('2', 'runtime'), ('3', 'model'), ('0', None)]:
            with self.subTest(answer=answer), patch.object(sys.stdin, 'isatty', return_value=True), \
                    patch('builtins.input', side_effect=['', 'invalid', answer]):
                self.assertEqual(setup.select_component(None), expected)
        self.assertIn(sys.executable, self.stderr.getvalue())

    def test_eof_or_interrupt_cancels_menu(self):
        for error in (EOFError, KeyboardInterrupt):
            with patch.object(sys.stdin, 'isatty', return_value=True), patch('builtins.input', side_effect=error):
                self.assertIsNone(setup.select_component(None))

    def test_noninteractive_and_json_require_explicit_component(self):
        with patch.object(sys.stdin, 'isatty', return_value=False), patch.object(setup, 'install') as install:
            with self.assertRaisesRegex(ValueError, '--component'):
                setup.select_component(None)
            self.assertEqual(setup.select_component('model'), 'model')
            install.assert_not_called()
        with patch.object(sys.stdin, 'isatty', return_value=True):
            with self.assertRaisesRegex(ValueError, '--component'):
                setup.select_component(None, json_output=True)

    def test_runtime_constraints_match_base_and_optional_package_metadata(self):
        project = Path(__file__).resolve().parents[2] / 'pyproject.toml'
        text = project.read_text()
        base = ast.literal_eval(re.search(r'^dependencies = (.+)$', text, re.M)[1])
        optional = ast.literal_eval(re.search(r'^autoqa = (.+)$', text, re.M)[1])
        self.assertEqual(set(setup.RUNTIME_REQUIREMENTS), set(base + optional))

    def test_pip_uses_same_interpreter_binary_wheels_and_stderr_then_fresh_check(self):
        calls = []
        def run(command, **kwargs):
            calls.append((command, kwargs))
            if command[1:3] == ['-m', 'pip']:
                return subprocess.CompletedProcess(command, 0)
            return subprocess.CompletedProcess(command, 0, '{"ready": true, "versions": {}}', '')
        # A real temporary stream has a file descriptor, just like CLI stderr.
        with tempfile.TemporaryFile(mode='w+') as log, contextlib.redirect_stderr(log), \
                patch.object(setup.subprocess, 'run', side_effect=run):
            self.assertTrue(setup.install_runtime()['ready'])
            self.assertIs(calls[0][1]['stdout'], log)
            self.assertIs(calls[0][1]['stderr'], log)
        self.assertTrue(all(command[0] == sys.executable for command, _ in calls))
        self.assertIn('--no-input', calls[0][0])
        self.assertIn('--only-binary=:all:', calls[0][0])
        self.assertEqual(calls[1][0][1], '-c')

    def test_pip_failure_does_not_claim_runtime_ready(self):
        with patch.object(setup.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1)), \
                patch.object(setup, 'read_process_json') as check:
            with self.assertRaisesRegex(ValueError, 'pip 安装失败'):
                setup.install_runtime()
            check.assert_not_called()

    def test_new_runtime_import_failure_is_reported(self):
        with patch.object(setup.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)), \
                patch.object(setup, 'read_process_json', return_value={'ready': False, 'issues': ['broken native library']}):
            with self.assertRaisesRegex(ValueError, 'broken native library'):
                setup.install_runtime()

    def test_all_installs_both_and_requires_real_doctor_success(self):
        for ready in (True, False):
            with self.subTest(ready=ready), patch.object(setup, 'install_runtime', return_value={'ready': True}), \
                    patch.object(setup, 'read_process_json', side_effect=[
                        {'summary': {'cached': True, 'model': 'local'}},
                        {'summary': {'ready': ready, 'issues': [] if ready else ['inference failed']}}
                    ]) as child:
                result = setup.install('all', self.directory)
            self.assertEqual(result['completed'], ready)
            self.assertEqual(result['ready'], ready)
            self.assertTrue(result['components']['model']['cached'])
            self.assertEqual(child.call_args_list[0].args[0][-1], str(self.directory.resolve()))
            self.assertIn('model-doctor', child.call_args_list[1].args[0])

    def test_partial_component_install_success_is_distinct_from_whole_readiness(self):
        for component in ('runtime', 'model'):
            responses = [{'summary': {'ready': False, 'issues': ['other component missing']}}]
            if component == 'model':
                responses.insert(0, {'summary': {'cached': False}})
            with self.subTest(component=component), patch.object(setup, 'install_runtime', return_value={'ready': True}) as runtime, \
                    patch.object(setup, 'read_process_json', side_effect=responses):
                result = setup.install(component, self.directory)
                if component == 'model':
                    runtime.assert_not_called()
            self.assertTrue(result['completed'])
            self.assertFalse(result['ready'])
            self.assertEqual(set(result['components']), {component})
            self.assertIn('尚未就绪', setup.setup_message(result))

    def test_component_failure_preserves_other_success_and_checks_readiness(self):
        for error in (ValueError('pip failed'), OSError('missing pip'), subprocess.TimeoutExpired('pip', 900)):
            with self.subTest(error=error), patch.object(setup, 'install_runtime', side_effect=error), \
                    patch.object(setup, 'read_process_json', side_effect=[
                        {'summary': {'cached': False}}, {'summary': {'ready': False, 'issues': ['no runtime']}}
                    ]):
                result = setup.install('all', self.directory)
            self.assertFalse(result['completed'])
            self.assertEqual(result['components']['runtime']['status'], 'failed')
            self.assertEqual(result['components']['model']['status'], 'completed')

    def test_corrupt_model_is_not_treated_as_success_and_doctor_timeout_is_recorded(self):
        with patch.object(setup, 'read_process_json', side_effect=[
                ValueError('checksum mismatch'), subprocess.TimeoutExpired('doctor', 60)]):
            result = setup.install('model', self.directory)
        self.assertFalse(result['completed'])
        self.assertFalse(result['ready'])
        self.assertIn('checksum mismatch', setup.setup_message(result))

    def test_process_failure_and_non_json_output_are_actionable(self):
        for code, output, expected in [(2, '{"error":{"message":"bad model"}}', 'bad model'),
                                       (-1, '', '未返回 JSON')]:
            process = subprocess.CompletedProcess([], code, output, 'child diagnostic\n')
            with patch.object(setup.subprocess, 'run', return_value=process):
                with self.assertRaisesRegex(ValueError, expected):
                    setup.read_process_json(['python'], 10)
        self.assertIn('child diagnostic', self.stderr.getvalue())

    def test_json_setup_is_single_envelope_and_excluded_from_tasks(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), patch.object(setup, 'install') as install:
            code = main(['--json', 'qa', 'setup'])
            install.assert_not_called()
        self.assertEqual(code, 2)
        self.assertFalse(json.loads(output.getvalue())['ok'])
        self.assertNotIn('qa.setup', catalog())
        self.assertNotIn('qa.model-download', catalog())

    def test_setup_json_records_partial_failure_and_cancel_performs_no_install(self):
        failed = {'completed': False, 'ready': False, 'components': {
            'runtime': {'status': 'failed', 'error': 'pip failed'}}, 'check': {'issues': []}}
        output = io.StringIO()
        with contextlib.redirect_stdout(output), patch.object(setup, 'install', return_value=failed):
            self.assertEqual(main(['--json', 'qa', 'setup', '--component', 'all']), 3)
        envelope = json.loads(output.getvalue())
        self.assertFalse(envelope['ok'])
        self.assertEqual(envelope['summary'], failed)
        with contextlib.redirect_stdout(io.StringIO()), patch.object(setup, 'select_component', return_value=None), \
                patch.object(setup, 'install') as install:
            self.assertEqual(main(['qa', 'setup']), 0)
            install.assert_not_called()
