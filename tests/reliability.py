"""Hardware-free regression tests; subprocess, power and sleep are always mocked."""
import copy
import fcntl
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import reliability as r


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


watch = load('watchdog_tests', SCRIPTS / 'matrix-network-watchdog.py')


class ReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.job = {'id': 'owned', 'status': 'failed', 'failure': 'Remote command failed (255)',
                    'directory': '/unused/run-1', 'artifact': {'sourceHash': 'fixture'}}
        self.calls = []
        self.status = '\nKERNEL\ntest\nBENCHMARK_PROCESSES\n\nMEMORY\nfixture'

    def transport(self, argv, **kwargs):
        op = argv[2]; self.calls.append(tuple(argv[2:]))
        output = {'runners': '(无 runner/minicom 进程)', 'serial-owner': '无 minicom/picocom/screen 进程',
                  'status': self.status}.get(op, '')
        return subprocess.CompletedProcess(argv, 0, output, '')

    def recover(self, transport=None):
        return r.recover_communication(self.job, Path('/fixture/k3-auto'),
                                      lambda: r.reserve_recovery(self.job, 1, lambda: self.calls.append(('persist-budget',))))

    def test_failure_classification(self):
        for error, expected in [('Remote command failed (255)', 'communication'),
                                ('SSH deadline exceeded', 'communication'),
                                ('Kernel panic; Remote command failed (255)', 'kernel'),
                                ('Oops: Remote command failed (255)', 'kernel'),
                                ('Board rebooted; SSH deadline exceeded', 'reboot'),
                                ('Permission denied; Remote command failed (255)', 'authentication'),
                                ('frequency restore failed; SSH deadline exceeded', 'frequency'),
                                ('Recovery refused: serial ownership unknown', 'resource'),
                                ('Image hash mismatch', 'identity'), ('missing evidence', 'unknown')]:
            with self.subTest(error=error): self.assertEqual(r.failure_kind(error), expected)
        self.assertEqual(r.failure_kind('Remote command failed (255)', {'cleanup': False}), 'evidence')

    def test_default_disabled_and_budget_durable(self):
        self.assertFalse(r.retry_available(self.job, 0))
        events = []; r.reserve_recovery(self.job, 1, lambda: events.append(copy.deepcopy(self.job)))
        restored = json.loads(json.dumps(events[-1]))
        self.assertFalse(r.retry_available(restored, 1))
        with self.assertRaises(r.RecoveryRefused): r.reserve_recovery(restored, 1, lambda: None)
        with self.assertRaises(ValueError): r.retry_available(self.job, 2)
        self.assertFalse(r.retry_available({'attempts': [{'id': 'prior'}]}, 1))

    def test_recovery_order_and_persistence_before_power(self):
        with patch.object(r.subprocess, 'run', side_effect=self.transport), patch.object(r.time, 'sleep') as sleep:
            self.recover()
        budget = self.calls.index(('persist-budget',)); off = self.calls.index(('power', 'off'))
        self.assertLess(budget, off)
        self.assertEqual([c for c in self.calls if c[0] == 'power'], [('power', 'off'), ('power', 'on')])
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [30, 45])
        jumps = [c[2] for c in self.calls if c[0] == 'jump']
        self.assertIn('p.mkdir(mode=0o700)', jumps[0]); self.assertIn("'runner.pid'", jumps[0])
        self.assertIn('owned', jumps[-1]); self.assertIn('p.rmdir()', jumps[-1])

    def test_busy_or_unknown_resources_never_power_cycle(self):
        for operation, stdout, stderr in [('runners', 'other runner', ''),
                                         ('serial-owner', '', ''),
                                         ('serial-owner', '无 minicom/picocom/screen 进程', 'fuser: Permission denied'),
                                         ('status', '\nboard_ssh_failed=255\n', ''),
                                         ('status', '\nKERNEL\ntest\nBENCHMARK_PROCESSES\nRun\nMEMORY\n', '')]:
            self.calls = []
            def transport(argv, **kwargs):
                if argv[2] == operation:
                    self.calls.append(tuple(argv[2:]))
                    return subprocess.CompletedProcess(argv, 0, stdout, stderr)
                return self.transport(argv, **kwargs)
            with self.subTest(operation=operation, stdout=stdout), patch.object(r.subprocess, 'run', side_effect=transport):
                with self.assertRaises(r.RecoveryRefused): self.recover()
                self.assertFalse(any(c[0] == 'power' for c in self.calls))

    def test_foreign_lease_or_live_pid_refused(self):
        def transport(argv, **kwargs):
            if argv[2] == 'jump':
                self.calls.append(tuple(argv[2:])); return subprocess.CompletedProcess(argv, 1, '', 'assertion failed')
            return self.transport(argv, **kwargs)
        with patch.object(r.subprocess, 'run', side_effect=transport):
            with self.assertRaises(r.RecoveryRefused): self.recover()
        self.assertFalse(any(c[0] == 'power' for c in self.calls))

    def test_network_wait_does_not_spend_budget(self):
        remaining = [2]
        def transport(argv, **kwargs):
            if remaining[0]:
                remaining[0] -= 1; return subprocess.CompletedProcess(argv, 255, '', 'connection timed out')
            return self.transport(argv, **kwargs)
        with patch.object(r.subprocess, 'run', side_effect=transport), patch.object(r.time, 'sleep') as sleep:
            r.recover_with_wait(self.job, 1, Path('/fixture/k3-auto'), lambda: None)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [300, 300, 30, 45])
        self.assertEqual(self.job['communicationRecoveryAttempts'], 1)
        self.assertTrue(self.job['recoveryPrepared'])

    def test_authentication_does_not_become_network_wait(self):
        def denied(argv, **kwargs): return subprocess.CompletedProcess(argv, 255, '', 'Permission denied (publickey)')
        with patch.object(r.subprocess, 'run', side_effect=denied), patch.object(r.time, 'sleep') as sleep:
            with self.assertRaises(r.RecoveryRefused):
                r.recover_with_wait(self.job, 1, Path('/fixture/k3-auto'), lambda: None)
        sleep.assert_not_called()

    def test_uncertain_power_outcome_exhausts_budget(self):
        def transport(argv, **kwargs):
            if argv[2] == 'power': raise subprocess.TimeoutExpired(['credential-placeholder'], 90)
            return self.transport(argv, **kwargs)
        with patch.object(r.subprocess, 'run', side_effect=transport):
            with self.assertRaises(r.RecoveryRefused) as caught: self.recover()
        self.assertNotIn('credential-placeholder', str(caught.exception))
        self.assertFalse(r.retry_available(self.job, 1))

    def test_archive_keeps_failure_and_clears_stale_success(self):
        self.job.update(scores={'16': 123}, checks={'cleanup': False}, frequency={'lock': {}}, communicationRecoveryAttempts=1)
        old = self.job['id']; spec = {'id': old, 'directory': self.job['directory'], 'recoverLeaseOwner': 'old'}
        r.next_attempt(self.job, spec)
        self.assertNotEqual(self.job['id'], old)
        self.assertEqual(self.job['attempts'][0]['id'], old)
        self.assertEqual(self.job['attempts'][0]['checks'], {'cleanup': False})
        self.assertIsNone(self.job['checks']); self.assertIsNone(self.job['scores'])
        self.assertNotIn('recoverLeaseOwner', spec); self.assertFalse(r.retry_available(self.job, 1))

    def execute(self, maximum, error='Remote command failed (255)', repeat_failure=False):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / 'run-1'; directory.mkdir()
            self.job['directory'] = str(directory)
            spec = {'id': self.job['id'], 'directory': str(directory)}
            (directory / 'spec.json').write_text(json.dumps(spec))
            attempts = []
            def transport(argv, **kwargs):
                if str(argv[1]).endswith('real-stage.py'):
                    attempts.append(argv)
                    path = Path(argv[2]).parent
                    if len(attempts) == 1 or repeat_failure:
                        (path / 'failure.json').write_text(json.dumps({'error': error}))
                        return subprocess.CompletedProcess(argv, 1)
                    return subprocess.CompletedProcess(argv, 0)
                return self.transport(argv, **kwargs)
            with patch.object(r.subprocess, 'run', side_effect=transport), patch.object(r.time, 'sleep'):
                result = r.execute_test(self.job, spec, Path('/fixture/workflow'), Path('/fixture/k3-auto'), lambda: None, maximum)
            self.assertTrue((directory / 'failure.json').exists())
            return result.returncode, len(attempts)

    def test_default_never_retries(self): self.assertEqual(self.execute(0), (1, 1))
    def test_one_retry_has_new_id_and_preserves_evidence(self):
        self.assertEqual(self.execute(1), (0, 2)); self.assertEqual(len(self.job['attempts']), 1)
    def test_failed_retry_does_not_retry_again(self): self.assertEqual(self.execute(1, repeat_failure=True), (1, 2))
    def test_panic_never_retries(self): self.assertEqual(self.execute(1, 'Kernel panic; Remote command failed (255)'), (1, 1))

    def test_watchdog_no_authorization_no_hardware(self):
        with patch.object(watch.subprocess, 'Popen') as spawn:
            with self.assertRaises(RuntimeError): watch.check_once(Path('/unused'), None)
            spawn.assert_not_called()

    def test_watchdog_active_dispatcher_never_intervenes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with (root / 'dispatcher.lock').open('a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                self.assertIn('active', watch.check_once(root, None, authorized=True))

    def test_watchdog_exhausted_budget_no_power_no_spawn(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); self.job['communicationRecoveryAttempts'] = 1
            (root / 'state.json').write_text(json.dumps({'status': 'blocked', 'runs': [self.job], 'current': None}))
            with patch.object(watch.subprocess, 'Popen') as spawn:
                self.assertIn('exhausted', watch.check_once(root, None, authorized=True)); spawn.assert_not_called()

    def test_watchdog_resumes_prepared_recovery_without_another_reset(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); self.job.update(communicationRecoveryAttempts=1, recoveryPrepared=True)
            (root / 'state.json').write_text(json.dumps({'status': 'blocked', 'runs': [self.job], 'current': None}))
            matrix = SimpleNamespace(__file__=str(SCRIPTS / 'unixbench-matrix.py'),
                                     save_state=lambda state: None, recover_communication=lambda *args: self.fail('duplicate power cycle'))
            with patch.object(watch.subprocess, 'Popen', return_value=SimpleNamespace(pid=123)) as spawn:
                self.assertIn('Resumed', watch.check_once(root, matrix, authorized=True)); spawn.assert_called_once()

    def test_competing_dispatcher_does_not_overwrite_active_state(self):
        matrix = load('matrix_lock_tests', SCRIPTS / 'unixbench-matrix.py')
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); state_path = root / 'state.json'
            original = {'status': 'running', 'current': 'other-run', 'dispatcherPid': -1}
            state_path.write_text(json.dumps(original))
            with (root / 'dispatcher.lock').open('a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                with patch.object(matrix, 'MATRIX_ROOT', root), patch.object(matrix, 'STATE_PATH', state_path), \
                     patch.object(matrix, 'LOCK_PATH', root/'dispatcher.lock'), \
                     patch.object(matrix, 'make_plan', side_effect=AssertionError('must not plan or access hardware')), \
                     patch.object(sys, 'argv', ['unixbench-matrix.py', '--run']):
                    self.assertEqual(matrix.main(), 1)
            self.assertEqual(json.loads(state_path.read_text()), original)

    def test_multiple_failures_or_panic_not_selected(self):
        self.assertIsNone(watch.communication_failure({'runs': [self.job, copy.deepcopy(self.job)]}))
        self.job['failure'] = 'Kernel panic; Remote command failed (255)'
        self.assertIsNone(watch.communication_failure({'runs': [self.job]}))


if __name__ == '__main__':
    unittest.main()
