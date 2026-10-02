#!/usr/bin/env python3
"""Authorized isolated normal-gate builds, then SIP reproduction and 18 runs."""
import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import uuid

from reliability import execute_test, failure_kind

REPO = Path(__file__).resolve().parents[1]
ROOT = REPO / '.workflow/normal-matrix-20261002'
SOURCE = Path('/tmp/k3-iee-test-20260930/src')
BUILD_ROOT = Path('/tmp/k3-iee-normal-20261002')
OLD = REPO / '.workflow/unixbench-matrix-20260930/automated-21-restart-235519'
ARTIFACTS = Path('/tmp/k3-iee-test-20260930/artifacts')
K3_ROOT = Path(os.environ.get('K3_ROOT', str(REPO.parent / 'k3-auto'))).resolve()
COMMIT = 'c4381d44062ede9e76b8315c33a7711cce29333c'
CONFIGS = [f'{gate}-{feature}' for gate in ('csrrsi-normal', 'swmmu-normal') for feature in ('ptp', 'credp', 'sip')]
state = {'status': 'building', 'builds': {}, 'runs': [], 'completed': 0, 'total': 18}
CAMPAIGN_OWNED = False


def save():
    state['updatedAt'] = time.strftime('%Y-%m-%dT%H:%M:%S%z')
    temp = ROOT / 'state.json.tmp'
    temp.write_text(json.dumps(state, indent=2) + '\n')
    temp.replace(ROOT / 'state.json')


def digest(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def build(name):
    out = BUILD_ROOT / name
    out.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(ARTIFACTS / 'baseline/config', out / '.config')
    # A seed file alone is insufficient: Kbuild if_changed would regenerate it.
    # This campaign uses the exact same SOURCE path/seed command as the baseline.
    seed = Path('/tmp/k3-iee-test-20260930/build/scripts/basic/randstruct.seed')
    (out / 'scripts/basic').mkdir(parents=True)
    (out / 'include/generated').mkdir(parents=True)
    shutil.copyfile(seed, out / 'scripts/basic/randstruct.seed')
    shutil.copyfile(seed.with_name('.randstruct.seed.cmd'), out / 'scripts/basic/.randstruct.seed.cmd')
    shutil.copyfile(seed.parents[2] / 'include/generated/randstruct_hash.h', out / 'include/generated/randstruct_hash.h')
    argv = [str(SOURCE / 'scripts/config'), '--file', str(out / '.config')]
    for option in ('IEE', 'PTP'):
        argv += ['-e', option]
    for option in ('IEE_GATE_SWMMU_PRO', 'IEE_GATE_CSRRSI_FAST', 'IEE_TEST', 'PTP_TEST'):
        argv += ['-d', option]
    csrrsi = name.startswith('csrrsi')
    argv += ['-e' if csrrsi else '-d', 'IEE_GATE_CSRRSI', '-d' if csrrsi else '-e', 'IEE_GATE_SWMMU']
    argv += ['-d' if name.endswith('-ptp') else '-e', 'CREDP', '-e' if name.endswith('-sip') else '-d', 'IEE_SIP']
    subprocess.run(argv, check=True)
    cmd = ['make', '-C', str(SOURCE), 'O=' + str(out), 'ARCH=riscv', 'CROSS_COMPILE=riscv64-unknown-linux-gnu-']
    env = dict(os.environ, KBUILD_BUILD_USER='jingyu', KBUILD_BUILD_HOST='zgclab')
    with open(out / 'build.log', 'ab', buffering=0) as log:
        subprocess.run(cmd + ['olddefconfig'], env=env, stdout=log, stderr=subprocess.STDOUT, check=True)
        config = (out / '.config').read_text()
        required = ['CONFIG_IEE=y', 'CONFIG_PTP=y', 'CONFIG_IEE_GATE_CSRRSI=y' if csrrsi else 'CONFIG_IEE_GATE_SWMMU=y']
        required += ['CONFIG_CREDP=y'] if not name.endswith('-ptp') else []
        required += ['CONFIG_IEE_SIP=y'] if name.endswith('-sip') else []
        if any(s not in config.splitlines() for s in required) or 'CONFIG_IEE_GATE_CSRRSI_FAST=y' in config or 'CONFIG_IEE_GATE_SWMMU_PRO=y' in config:
            raise RuntimeError('Normal configuration validation failed: ' + name)
        subprocess.run(cmd + ['-j16', 'Image'], env=env, stdout=log, stderr=subprocess.STDOUT, check=True)
    if (out / 'scripts/basic/randstruct.seed').read_bytes() != seed.read_bytes():
        raise RuntimeError('Effective RANDSTRUCT seed changed; refusing benchmark handoff')
    if seed.read_text().strip() not in (out / 'scripts/gcc-plugins/randomize_layout_seed.h').read_text():
        raise RuntimeError('GCC plugin does not use the baseline RANDSTRUCT seed')
    artifact = out / 'artifact'; artifact.mkdir()
    shutil.copyfile(out / 'arch/riscv/boot/Image', artifact / 'Image')
    shutil.copyfile(out / '.config', artifact / 'config')
    notes = subprocess.check_output(['riscv64-unknown-linux-gnu-readelf', '-n', str(out / 'vmlinux')], text=True)
    build_id = re.search(r'Build ID:\s*([0-9a-f]+)', notes).group(1)
    report = {'kind': 'build', 'sourceCommit': COMMIT, 'artifactHash': digest(artifact / 'Image'),
              'configHash': digest(artifact / 'config'), 'buildId': build_id,
              'kernelRelease': (out / 'include/config/kernel.release').read_text().strip()}
    (artifact / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
    state['builds'][name] = {'status': 'complete', 'artifact': str(artifact), **report}; save()
    print('BUILT', name, flush=True)


def test(name, round_number, artifact, targets):
    run_id = 'exp-' + str(uuid.uuid4())
    directory = ROOT / 'runs' / name / f'run-{round_number}'
    directory.mkdir(parents=True, exist_ok=False)
    report = json.loads((artifact / 'result.json').read_text())
    job = {'id': run_id, 'config': name, 'round': round_number, 'status': 'running', 'directory': str(directory)}
    state['runs'].append(job); state['current'] = run_id; save()
    spec = {'stage': 'test', 'id': run_id, 'directory': str(directory), 'k3Root': str(K3_ROOT),
            'sourceHash': hashlib.sha256((COMMIT + ':' + report['configHash'] + ':' + report['artifactHash']).encode()).hexdigest(),
            'artifact': {'path': str(artifact / 'Image'), 'sha256': report['artifactHash']}, 'frequencyTargets': targets}
    (directory / 'spec.json').write_text(json.dumps(spec, indent=2) + '\n')
    print('START', name, round_number, run_id, flush=True)
    def persist_attempt():
        state['current'] = job['id']; save()
    completed = execute_test(job, spec, REPO, spec['k3Root'], persist_attempt, state.get('communicationRetries', 0))
    rc = completed.returncode
    directory = Path(job['directory'])
    result = json.loads((directory / 'result.json').read_text()) if (directory / 'result.json').exists() else {}
    vector = ((result.get('frequency') or {}).get('lock') or {}).get('targets')
    job.update(status='complete' if rc == 0 and result.get('checks') and all(result['checks'].values()) and vector == targets else 'failed',
               exitCode=rc, scores=result.get('scores'), checks=result.get('checks'), frequency=result.get('frequency'))
    if job['status'] == 'failed':
        job['failure'] = (directory / 'failure.json').read_text() if (directory / 'failure.json').exists() else 'missing result or frequency vector mismatch'
        job['failureKind'] = failure_kind(job['failure'], job.get('checks'))
    state['current'] = None; save()
    return job


def release_prebenchmark_failure(job):
    """Proceed only after a failed boot with no frequency changes and acknowledged relay-off."""
    report_path = Path(job['directory']) / 'evidence.json'
    report = json.loads(report_path.read_text())
    if report.get('lastPowerAction') != 'off' or (report.get('frequency') or {}).get('lock') is not None:
        raise RuntimeError('Failed run cleanup needs attention')
    ctl = [sys.executable, str(K3_ROOT / 'scripts/k3ctl.py')]
    def call(*args):
        return subprocess.check_output(ctl + list(args), cwd=K3_ROOT, text=True, timeout=90)
    if call('runners').strip() != '(无 runner/minicom 进程)' or call('serial-owner').strip() != '无 minicom/picocom/screen 进程':
        raise RuntimeError('Failed boot still holds resources')
    import shlex
    code = ("from pathlib import Path; p=Path('/tmp/k3-agent-workflow-board.lock'); "
            f"assert (p/'owner').read_text().strip()=={job['id']!r}; "
            "assert not (p/'runner.pid').exists(); (p/'owner').unlink(); p.rmdir()")
    call('jump', '--', 'python3 -c ' + shlex.quote(code))


def main(communication_retries=0):
    global CAMPAIGN_OWNED
    state['communicationRetries'] = communication_retries
    os.umask(0o077); ROOT.mkdir(parents=True, exist_ok=True)
    with open(ROOT / 'campaign.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if (ROOT / 'state.json').exists():
            raise RuntimeError('Campaign already exists; refusing duplicate work')
        CAMPAIGN_OWNED = True
        if subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=SOURCE, text=True).strip() != COMMIT:
            raise RuntimeError('Wrong source commit')
        if subprocess.check_output(['git', 'status', '--porcelain'], cwd=SOURCE, text=True).strip():
            raise RuntimeError('Build source is not clean')
        save()
        for name in CONFIGS:
            build(name)
        state['status'] = 'waiting-for-original-matrix'; save()
        while True:
            prior = json.loads((OLD / 'state.json').read_text())
            if prior['status'] in ('complete', 'complete-with-skips'):
                break
            if prior['status'] == 'blocked':
                print('Original matrix blocked; waiting for its recovery/operator', flush=True)
            time.sleep(300)
        # Original dispatcher retains its lock through final cleanup.
        with open(OLD / 'dispatcher.lock', 'a') as old_lock:
            fcntl.flock(old_lock, fcntl.LOCK_EX)
            targets = prior['frequencyTargets']
            state['frequencyTargets'] = targets; state['status'] = 'testing'; save()
            repro = test('swmmu-pro-sip-reproduction', 1, ARTIFACTS / 'swmmu-pro-sip', targets)
            if repro['status'] == 'failed':
                release_prebenchmark_failure(repro)
                print('SIP reproduction failed again; evidence retained, proceeding to normal-gate matrix', flush=True)
            for round_number in range(1, 4):
                for name in CONFIGS:
                    job = test(name, round_number, BUILD_ROOT / name / 'artifact', targets)
                    if job['status'] != 'complete':
                        raise RuntimeError('Normal matrix blocked: ' + name)
                    state['completed'] += 1; save()
            state['averages'] = {}
            for name in CONFIGS:
                jobs = [j for j in state['runs'] if j['config'] == name]
                state['averages'][name] = {key: round(sum(j['scores'][key] for j in jobs) / 3, 2) for key in ('1', '16')}
            state['status'] = 'complete'; save()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--communication-retries', type=int, choices=(0, 1), default=0,
                        help='explicit power-cycle retry authorization; maximum one per round')
    args = parser.parse_args()
    try:
        main(args.communication_retries)
    except Exception as exc:
        # Do not overwrite a previously started campaign on duplicate invocation.
        if CAMPAIGN_OWNED:
            state.update(status='blocked', current=None, failure=str(exc)); save()
        print('CAMPAIGN BLOCKED:', type(exc).__name__, str(exc), flush=True)
        sys.exit(1)
