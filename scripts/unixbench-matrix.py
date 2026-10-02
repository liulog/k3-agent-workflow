#!/usr/bin/env python3
"""Sequential, persistent runner for the already-built K3 TEST.md Image matrix.

This script never builds or installs anything. It calls the fixed real-stage.py
executor one image/run at a time. Hardware retries are disabled by default;
explicit opt-in permits one communication recovery, never panic/evidence retries.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid

from reliability import (execute_test, failure_kind, next_attempt, recover_communication as recover_once,
                         recover_with_wait, reserve_recovery, retry_available)

REPO = Path(__file__).resolve().parents[1]
ARTIFACT_ROOT = Path(os.environ.get('K3_MATRIX_ARTIFACT_ROOT', '/tmp/k3-iee-test-20260930/artifacts')).resolve()
MATRIX_ROOT = Path(os.environ.get('K3_MATRIX_ROOT', str(REPO / '.workflow/unixbench-matrix-20260930/automated-21'))).resolve()
STATE_PATH = MATRIX_ROOT / 'state.json'
STATE_OWNED = False
LOCK_PATH = MATRIX_ROOT / 'dispatcher.lock'
STAGE = REPO / 'scripts/real-stage.py'
K3_ROOT = Path(os.environ.get('K3_ROOT', str(REPO.parent / 'k3-auto'))).resolve()
EXPECTED_COMMIT = 'c4381d44062ede9e76b8315c33a7711cce29333c'
RECOVER_OWNER = os.environ.get('K3_MATRIX_RECOVER_OWNER', '')
RECOVER_FAILURE_EVIDENCE = Path(os.environ.get('K3_MATRIX_RECOVER_FAILURE_EVIDENCE', str(REPO / '.workflow/unixbench-matrix-20260930/automated-21/runs/baseline/run-1/remote.log')))
CONFIGS = [
    ('baseline', 'baseline'),
    ('swmmu-pro-ptp', 'SWMMU-PRO-PTP'),
    ('swmmu-pro-credp', 'SWMMU-PRO-CREDP'),
    ('swmmu-pro-sip', 'SWMMU-PRO-SIP'),
    ('csrrsi-fast-ptp', 'CSRRSI-FAST-PTP'),
    ('csrrsi-fast-credp', 'CSRRSI-FAST-CREDP'),
    ('csrrsi-fast-sip', 'CSRRSI-FAST-SIP'),
]


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def atomic_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    os.replace(temporary, path)


def artifact_info(name, baseline_report, persist):
    directory = ARTIFACT_ROOT / name
    image = directory / 'Image'
    config_file = directory / 'config.sha256'
    if not image.is_file() or image.is_symlink() or image.stat().st_size == 0:
        raise RuntimeError(f'{name}: missing/invalid prebuilt Image')
    if not config_file.is_file():
        raise RuntimeError(f'{name}: missing config.sha256')
    config_hash = config_file.read_text().split()[0]
    if not (directory / 'config').is_file() or sha256(directory / 'config') != config_hash:
        raise RuntimeError(f'{name}: config contents do not match config.sha256')
    report_path = directory / 'result.json'
    if report_path.exists():
        report = json.loads(report_path.read_text())
    else:
        build_id_file = directory / 'build-id.txt'
        if not build_id_file.is_file():
            raise RuntimeError(f'{name}: missing build-id.txt')
        match = re.search(r'Build ID:\s*([0-9a-f]+)', build_id_file.read_text())
        if not match:
            raise RuntimeError(f'{name}: malformed build-id.txt')
        report = {
            'kind': 'build',
            'sourceCommit': EXPECTED_COMMIT,
            'artifactHash': sha256(image),
            'configHash': config_hash,
            'buildId': match.group(1),
            'kernelRelease': baseline_report['kernelRelease'],
        }
        if persist:
            atomic_json(report_path, report)
    actual_hash = sha256(image)
    if report.get('sourceCommit') != EXPECTED_COMMIT:
        raise RuntimeError(f'{name}: build report source commit mismatch')
    if report.get('artifactHash') != actual_hash:
        raise RuntimeError(f'{name}: Image hash does not match build report')
    if report.get('configHash') != config_hash:
        raise RuntimeError(f'{name}: config hash does not match build report')
    if not report.get('buildId') or report.get('kernelRelease') != baseline_report.get('kernelRelease'):
        raise RuntimeError(f'{name}: incomplete build identity')
    source_hash = hashlib.sha256(
        f"{EXPECTED_COMMIT}:{config_hash}:{actual_hash}".encode()).hexdigest()
    return {'name': name, 'label': name, 'image': str(image), 'artifactHash': actual_hash,
            'configHash': config_hash, 'buildId': report['buildId'],
            'kernelRelease': report['kernelRelease'], 'sourceHash': source_hash}


def make_plan(persist):
    baseline_path = ARTIFACT_ROOT / 'baseline/result.json'
    baseline_report = json.loads(baseline_path.read_text())
    if baseline_report.get('sourceCommit') != EXPECTED_COMMIT:
        raise RuntimeError('Baseline build report is not from TEST.md source commit')
    artifacts = {name: artifact_info(name, baseline_report, persist) for name, _ in CONFIGS}
    jobs = []
    for round_number in range(1, 4):
        for name, label in CONFIGS:
            artifact = artifacts[name]
            run_id = 'exp-' + str(uuid.uuid4())
            run_dir = MATRIX_ROOT / 'runs' / name / f'run-{round_number}'
            jobs.append({
                'id': run_id, 'config': name, 'label': label, 'round': round_number,
                'directory': str(run_dir), 'artifact': artifact,
                'status': 'queued', 'exitCode': None, 'scores': None,
            })
    return jobs


def save_state(state):
    state['updatedAt'] = time.strftime('%Y-%m-%dT%H:%M:%S%z')
    atomic_json(STATE_PATH, state)


def recover_communication(job, state):
    """One watchdog attempt; read-only network failure leaves the budget intact."""
    maximum = state.get('automaticRetries', 0)
    if not retry_available(job, maximum):
        raise RuntimeError('Recovery refused: communication retry budget exhausted')
    def record(event):
        job.setdefault('recoveryEvents', []).append({'event': event, 'at': time.time()})
        save_state(state)
    recover_once(job, K3_ROOT, lambda: reserve_recovery(job, maximum, lambda: save_state(state)), record)
    job['recoveryPrepared'] = True
    save_state(state)


def run_matrix(resume=False, skip_config=None, retry_failed=False, communication_retries=0):
    global STATE_OWNED
    STATE_OWNED = False
    os.umask(0o077)
    MATRIX_ROOT.mkdir(parents=True, exist_ok=True)
    with open(LOCK_PATH, 'a') as lock_file:
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Matrix dispatcher already running')
        if resume:
            state = json.loads(STATE_PATH.read_text())
            if state['status'] != 'blocked' or state.get('current'):
                raise RuntimeError('Only a blocked, inactive matrix can be resumed')
            STATE_OWNED = True
            state['dispatcherPid'] = os.getpid()
            jobs = state['runs']
            if retry_failed:
                failed = [job for job in jobs if job['status'] in ('failed', 'recovering')]
                if len(failed) != 1 or failure_kind(failed[0].get('failure', ''), failed[0].get('checks')) != 'communication':
                    raise RuntimeError('Only one inactive communication failure may be retried here')
                state['automaticRetries'] = 1
                job = failed[0]
                if not job.get('recoveryPrepared'):
                    recover_with_wait(job, 1, K3_ROOT, lambda: save_state(state))
                next_attempt(job)
            elif not skip_config or any(j['status'] == 'failed' and j['config'] != skip_config for j in jobs):
                raise RuntimeError('Resume requires explicitly skipping or retrying the failed configuration')
            for job in jobs:
                if job['config'] == skip_config and job['status'] in ('queued', 'failed'):
                    job['priorStatus'] = job['status']
                    job['status'] = 'skipped'
                    job['skipReason'] = 'User authorized skipping failed configuration'
            state.update(status='running', current=None)
        else:
            if STATE_PATH.exists():
                raise RuntimeError(f'Matrix state already exists; refusing duplicate dispatch: {STATE_PATH}')
            jobs = make_plan(persist=True)
            STATE_OWNED = True
            state = {'status': 'running', 'startedAt': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
                     'total': len(jobs), 'completed': 0, 'current': None, 'runs': jobs,
                     'frequencyTargets': None, 'monitorIntervalSeconds': 4800, 'automaticRetries': communication_retries}
        if communication_retries:
            state['automaticRetries'] = communication_retries
        state['dispatcherPid'] = os.getpid()
        state['communicationRecovery'] = bool(state.get('automaticRetries', 0))
        state['recoveryPolicy'] = 'Opt-in: one persisted communication power-cycle retry per round; five-minute read-only network wait; no kernel/evidence retries'
        save_state(state)
        for index, job in enumerate(jobs):
            if job['status'] in ('complete', 'skipped'):
                continue
            if job['status'] != 'queued':
                raise RuntimeError('Refusing automatic retry of a non-queued run')
            run_dir = Path(job['directory'])
            run_dir.mkdir(parents=True, exist_ok=False)
            spec = {
                'stage': 'test', 'id': job['id'], 'sourceHash': job['artifact']['sourceHash'],
                'directory': str(run_dir), 'k3Root': str(K3_ROOT),
                'artifact': {'path': job['artifact']['image'], 'sha256': job['artifact']['artifactHash']},
            }
            if index == 0 and RECOVER_OWNER:
                spec['recoverLeaseOwner'] = RECOVER_OWNER
                spec['recoverFailureEvidencePath'] = str(RECOVER_FAILURE_EVIDENCE)
            if state.get('frequencyTargets'):
                spec['frequencyTargets'] = state['frequencyTargets']
            spec_path = run_dir / 'spec.json'
            atomic_json(spec_path, spec)
            job['status'] = 'running'
            job['startedAt'] = time.strftime('%Y-%m-%dT%H:%M:%S%z')
            state['current'] = job['id']
            save_state(state)
            print(f"[{index + 1}/{len(jobs)}] START {job['label']} run-{job['round']} {job['id']}", flush=True)
            def persist_attempt():
                state['current'] = job['id']
                save_state(state)
            completed = execute_test(job, spec, REPO, K3_ROOT, persist_attempt, state.get('automaticRetries', 0))
            run_dir = Path(job['directory'])
            job['exitCode'] = completed.returncode
            job['finishedAt'] = time.strftime('%Y-%m-%dT%H:%M:%S%z')
            result_path = run_dir / 'result.json'
            failure_path = run_dir / 'failure.json'
            if result_path.is_file():
                result = json.loads(result_path.read_text())
                job['scores'] = result.get('scores')
                job['checks'] = result.get('checks')
                job['frequency'] = result.get('frequency')
            frequency = job.get('frequency') or {}
            targets = (frequency.get('lock') or {}).get('targets')
            vector_ok = isinstance(targets, dict) and bool(targets) and (
                not state.get('frequencyTargets') or targets == state['frequencyTargets'])
            if completed.returncode != 0 or not job.get('checks') or not all(job['checks'].values()) or not vector_ok:
                job['status'] = 'failed'
                if failure_path.is_file():
                    job['failure'] = json.loads(failure_path.read_text()).get('error', 'stage failed')
                elif not vector_ok:
                    job['failure'] = 'per-policy frequency target vector missing or changed'
                else:
                    job['failure'] = f"fixed stage returned {completed.returncode} or evidence gates were incomplete"
                job['failureKind'] = failure_kind(job['failure'], job.get('checks'))
                state.update(status='blocked', blockedAt=job['finishedAt'], blockedRun=job['id'], current=None)
                save_state(state)
                print(f"[{index + 1}/{len(jobs)}] BLOCKED at {job['id']}: {job['failure']}", flush=True)
                return 1
            if not state.get('frequencyTargets'):
                state['frequencyTargets'] = targets
            job['status'] = 'complete'
            state['completed'] = sum(j['status'] == 'complete' for j in jobs)
            state['current'] = None
            save_state(state)
            print(f"[{index + 1}/{len(jobs)}] COMPLETE {job['id']} scores={job['scores']}", flush=True)
        averages = {}
        for name, label in CONFIGS:
            group = [job for job in jobs if job['config'] == name and job['status'] == 'complete']
            if len(group) != 3:
                continue
            averages[name] = {
                'label': label,
                '1-copy': round(sum(j['scores']['1'] for j in group) / 3, 2),
                '16-copy': round(sum(j['scores']['16'] for j in group) / 3, 2),
                'runs': [j['id'] for j in group],
            }
        state.update(status='complete-with-skips' if any(j['status'] == 'skipped' for j in jobs) else 'complete', finishedAt=time.strftime('%Y-%m-%dT%H:%M:%S%z'),
                     current=None, averages=averages)
        save_state(state)
        print('MATRIX COMPLETE', json.dumps(averages, ensure_ascii=False), flush=True)
        return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--plan', action='store_true', help='offline artifact/queue validation only')
    mode.add_argument('--run', action='store_true', help='run all tests sequentially; no builds; retries require explicit opt-in')
    mode.add_argument('--status', action='store_true', help='print persisted dispatcher state')
    mode.add_argument('--resume', action='store_true', help='continue queued runs after an explicitly authorized skip')
    parser.add_argument('--skip-config', choices=[name for name, _ in CONFIGS])
    parser.add_argument('--retry-failed', action='store_true', help='authorize one communication recovery for an inactive failed round')
    parser.add_argument('--communication-retries', type=int, choices=(0, 1), default=0,
                        help='explicit power-cycle retry authorization; default 0, maximum 1 per round')
    args = parser.parse_args()
    if args.plan:
        jobs = make_plan(persist=False)
        print(json.dumps({'total': len(jobs), 'order': [f"{j['label']} run-{j['round']}" for j in jobs],
                          'artifacts': {name: {'sha256': next(j['artifact']['artifactHash'] for j in jobs if j['config'] == name),
                                               'buildId': next(j['artifact']['buildId'] for j in jobs if j['config'] == name)}
                                        for name, _ in CONFIGS}}, ensure_ascii=False, indent=2))
        return 0
    if args.status:
        if not STATE_PATH.is_file():
            print(json.dumps({'status': 'not-dispatched', 'statePath': str(STATE_PATH)}, ensure_ascii=False))
            return 0
        print(STATE_PATH.read_text())
        return 0
    try:
        return run_matrix(resume=args.resume, skip_config=args.skip_config, retry_failed=args.retry_failed,
                          communication_retries=args.communication_retries)
    except Exception as exc:
        MATRIX_ROOT.mkdir(parents=True, exist_ok=True)
        if STATE_PATH.exists():
            try:
                state = json.loads(STATE_PATH.read_text())
                # A competing invocation must never overwrite the active dispatcher.
                if STATE_OWNED:
                    state.update(status='blocked', current=None, failure=str(exc))
                    save_state(state)
            except Exception:
                pass
        print(f'MATRIX DISPATCH BLOCKED: {exc}', file=sys.stderr, flush=True)
        return 1


if __name__ == '__main__':
    sys.exit(main())
