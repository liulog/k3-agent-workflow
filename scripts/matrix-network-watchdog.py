#!/usr/bin/env python3
"""Opt-in five-minute recovery supervisor; never cancels an active dispatcher."""
import argparse
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from reliability import NETWORK_WAIT_SECONDS, failure_kind, retry_available


def communication_failure(state):
    candidates = [j for j in state.get('runs', []) if j.get('status') in ('failed', 'recovering')]
    if len(candidates) != 1:
        return None
    job = candidates[0]
    errors = str(job.get('failure', '')) + ' ' + str(state.get('failure') or '')
    return job if failure_kind(errors, job.get('checks')) == 'communication' else None


def check_once(root, matrix, authorized=False):
    if not authorized:
        raise RuntimeError('Power-cycle recovery requires explicit authorization')
    with (root / 'dispatcher.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 'Dispatcher active; no intervention'
        state = json.loads((root / 'state.json').read_text())
        if state['status'] in ('complete', 'complete-with-skips'):
            return 'finished'
        if state['status'] != 'blocked' or state.get('current'):
            return 'No inactive blocked failure; no intervention'
        job = communication_failure(state)
        if job is None:
            return 'Non-network failure: operator attention required'
        # This watchdog resumes the prebuilt unixbench-matrix schema only.
        # Normal/pool-only entrypoints use the same inline recovery helper.
        if not job.get('artifact', {}).get('sourceHash'):
            return 'Unsupported matrix schema; no intervention'
        state['automaticRetries'] = 1
        if not job.get('recoveryPrepared'):
            if not retry_available(job, 1):
                return 'Communication retry budget exhausted; operator attention required'
            matrix.recover_communication(job, state)
        job['status'] = 'failed'
        state.update(failure=None, current=None, networkRecoveryIntervalSeconds=NETWORK_WAIT_SECONDS)
        matrix.save_state(state)
    # Child reacquires the dispatcher lock and revalidates the blocked state;
    # a competing dispatcher cannot be overwritten or receive a duplicate test.
    with (root / 'dispatcher.log').open('ab', buffering=0) as log:
        process = subprocess.Popen([sys.executable, str(matrix.__file__), '--resume', '--retry-failed'],
                                   cwd=Path(matrix.__file__).parent.parent, stdin=subprocess.DEVNULL,
                                   stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    (root / 'dispatcher.pid').write_text(str(process.pid) + '\n')
    return f'Resumed matrix: dispatcher PID {process.pid}'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--authorize-power-cycle-retry', action='store_true',
                        help='explicitly authorize one communication retry per round, never unlimited resets')
    args = parser.parse_args()
    if not args.authorize_power_cycle_retry:
        parser.error('--authorize-power-cycle-retry requires user authorization')
    root = args.root.resolve()
    os.umask(0o077)
    os.environ['K3_MATRIX_ROOT'] = str(root)
    os.environ['K3_MATRIX_RECOVER_OWNER'] = ''
    script = Path(__file__).with_name('unixbench-matrix.py')
    loader = importlib.util.spec_from_file_location('matrix', script)
    matrix = importlib.util.module_from_spec(loader); loader.loader.exec_module(matrix)
    with (root / 'network-watchdog.lock').open('a') as singleton:
        fcntl.flock(singleton, fcntl.LOCK_EX | fcntl.LOCK_NB)
        while True:
            try:
                outcome = check_once(root, matrix, authorized=True)
                if outcome == 'finished':
                    print('Matrix finished; supervisor exiting', flush=True)
                    return 0
                print(outcome, flush=True)
            except Exception as exc:
                # Probe failures do not consume power retries. Power attempts are
                # reserved durably before execution, even if their outcome is uncertain.
                print(f'Recovery unavailable/refused ({type(exc).__name__}); next check in five minutes', flush=True)
            time.sleep(NETWORK_WAIT_SECONDS)


if __name__ == '__main__':
    sys.exit(main())
