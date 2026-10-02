#!/usr/bin/env python3
"""Run the allocator-only control after normal-matrix; --build-and-run needs explicit build authorization."""
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

REPO = Path(__file__).resolve().parents[1]
ROOT = REPO / '.workflow/pool-only-matrix-20261002'
PREVIOUS = REPO / '.workflow/normal-matrix-20261002'
WORK = Path('/tmp/k3-ptp-pool-only-20261002')
SOURCE = WORK / 'src'
BASELINE = Path('/tmp/k3-iee-test-20260930/artifacts/baseline')
BASE_SEED = Path('/tmp/k3-iee-test-20260930/build/scripts/basic/randstruct.seed')
COMMIT = 'c4381d44062ede9e76b8315c33a7711cce29333c'
CONFIG_NAME = 'ptp-pool-only'
RUN_OWNED = False


def write_json(path, value):
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, indent=2) + '\n'); temp.replace(path)


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for data in iter(lambda: f.read(1024 * 1024), b''):
            h.update(data)
    return h.hexdigest()


def prepare():
    ROOT.mkdir(parents=True, exist_ok=True)
    if (ROOT / 'state.json').exists():
        raise RuntimeError('Already prepared; refusing overwrite')
    if subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=SOURCE, text=True).strip() != COMMIT:
        raise RuntimeError('Wrong worktree commit')
    patch = subprocess.check_output(['git', 'diff', '--binary'], cwd=SOURCE)
    (ROOT / 'source.patch').write_bytes(patch)
    shutil.copyfile(BASELINE / 'config', ROOT / 'config.requested')
    subprocess.run([str(SOURCE / 'scripts/config'), '--file', str(ROOT / 'config.requested'),
                    '-d', 'IEE', '-d', 'PTP', '-d', 'CREDP', '-d', 'IEE_SIP',
                    '-d', 'IEE_GATE_CSRRSI', '-d', 'IEE_GATE_CSRRSI_FAST',
                    '-d', 'IEE_GATE_SWMMU', '-d', 'IEE_GATE_SWMMU_PRO',
                    '-e', 'PTP_POOL_ONLY', '--set-val', 'PTP_RESERVE_ORDER', '12'], check=True)
    state = {'status': 'waiting-build-authorization', 'dependsOn': str(PREVIOUS), 'sourceCommit': COMMIT,
             'worktree': str(SOURCE), 'sourcePatchHash': sha(ROOT / 'source.patch'),
             'config': CONFIG_NAME, 'completed': 0, 'total': 3, 'runs': [],
             'queuedRuns': [1, 2, 3], 'randstructSeedFileHash': sha(BASE_SEED),
             'benchmark': {'copies': [1, 16], 'monitorSeconds': 4800,
                           'frequencyTargets': {'policy0': 2400000, 'policy8': 2000000}},
             'note': 'No build started. Compare to original baseline; CPU affinity must be recorded.'}
    write_json(ROOT / 'state.json', state)
    write_json(ROOT / 'queue.json', {'after': str(PREVIOUS / 'state.json'), 'jobs': [
        {'config': CONFIG_NAME, 'round': r, 'status': 'waiting-build-authorization'} for r in range(1, 4)]})
    print('Prepared allocator-only campaign:', ROOT)


def build(state):
    out = WORK / 'build'
    out.mkdir(exist_ok=False)
    shutil.copyfile(ROOT / 'config.requested', out / '.config')
    patch = subprocess.check_output(['git', 'diff', '--binary'], cwd=SOURCE)
    if hashlib.sha256(patch).hexdigest() != state['sourcePatchHash']:
        raise RuntimeError('Worktree patch changed since preparation')
    if sha(BASE_SEED) != state['randstructSeedFileHash']:
        raise RuntimeError('Baseline seed changed')
    env = dict(os.environ, KBUILD_RANDSTRUCT_SEED=BASE_SEED.read_text().strip(),
               KBUILD_BUILD_USER='jingyu', KBUILD_BUILD_HOST='zgclab')
    cmd = ['make', '-C', str(SOURCE), 'O=' + str(out), 'ARCH=riscv', 'CROSS_COMPILE=riscv64-unknown-linux-gnu-']
    with (out / 'build.log').open('ab', buffering=0) as log:
        subprocess.run(cmd + ['olddefconfig'], env=env, stdout=log, stderr=subprocess.STDOUT, check=True)
        config = (out / '.config').read_text().splitlines()
        for enabled in ['CONFIG_PTP_POOL=y', 'CONFIG_PTP_POOL_ONLY=y', 'CONFIG_PTP_RESERVE_ORDER=12']:
            if enabled not in config:
                raise RuntimeError('Missing configuration: ' + enabled)
        if any(l.endswith('=y') and (l.startswith('CONFIG_IEE') or l.startswith('CONFIG_PTP_TEST') or l in ('CONFIG_PTP=y', 'CONFIG_CREDP=y')) for l in config):
            raise RuntimeError('Protection/gate option unexpectedly enabled')
        def values(lines):
            return {l.split('=', 1)[0]: l.split('=', 1)[1] for l in lines if l.startswith('CONFIG_') and '=' in l}
        before, after = values((BASELINE / 'config').read_text().splitlines()), values(config)
        differences = {k: [before.get(k, 'n'), after.get(k, 'n')] for k in before.keys() | after.keys()
                       if before.get(k, 'n') != after.get(k, 'n')}
        if set(differences) != {'CONFIG_PTP_POOL', 'CONFIG_PTP_POOL_ONLY', 'CONFIG_PTP_RESERVE_ORDER'}:
            raise RuntimeError('Unexpected public config difference: ' + str(differences))
        write_json(ROOT / 'config-diff.json', differences)
        subprocess.run(cmd + ['-j16', 'Image'], env=env, stdout=log, stderr=subprocess.STDOUT, check=True)
    effective = out / 'scripts/basic/randstruct.seed'
    if effective.read_bytes() != BASE_SEED.read_bytes():
        raise RuntimeError('Effective RANDSTRUCT seed does not match baseline')
    seed = BASE_SEED.read_text().strip()
    if seed not in (out / 'scripts/gcc-plugins/randomize_layout_seed.h').read_text():
        raise RuntimeError('GCC plugin seed does not match baseline')
    # Prove the built image contains the pool and none of the gate/write hooks.
    symbols = subprocess.check_output(['riscv64-unknown-linux-gnu-nm', str(out / 'vmlinux')], text=True)
    names = {line.split()[-1] for line in symbols.splitlines() if line.split()}
    if not {'ptp_pg_alloc', 'ptp_pg_free', 'ptp_pagetable_alloc', 'ptp_pagetable_free'} <= names:
        raise RuntimeError('Missing pool symbols')
    forbidden = {'iee_gate', 'iee_gate_pro', 'iee_memset', 'iee_set_freeptr', 'ptp_enable_hooks',
                 'ptp_pgtable_pool_protect', 'ptp_static_pgtable_protect'}
    if forbidden & names:
        raise RuntimeError('Forbidden protection symbols: ' + str(forbidden & names))
    artifact = WORK / 'artifact'; artifact.mkdir()
    shutil.copyfile(out / 'arch/riscv/boot/Image', artifact / 'Image')
    shutil.copyfile(out / '.config', artifact / 'config')
    notes = subprocess.check_output(['riscv64-unknown-linux-gnu-readelf', '-n', str(out / 'vmlinux')], text=True)
    report = {'kind': 'build', 'sourceCommit': COMMIT, 'sourcePatchHash': state['sourcePatchHash'],
              'artifactHash': sha(artifact / 'Image'), 'configHash': sha(artifact / 'config'),
              'buildId': re.search(r'Build ID:\s*([0-9a-f]+)', notes).group(1),
              'kernelRelease': (out / 'include/config/kernel.release').read_text().strip(),
              'randstructSeedFileHash': sha(effective), 'noGateSymbols': True}
    write_json(artifact / 'result.json', report)
    state['build'] = {'artifact': str(artifact), **report}
    state['status'] = 'waiting-for-normal-matrix'; write_json(ROOT / 'state.json', state)


def run(communication_retries=0):
    global RUN_OWNED
    RUN_OWNED = False
    spec = importlib.util.spec_from_file_location('normal_matrix_helpers', REPO / 'scripts/normal-matrix.py')
    helper = importlib.util.module_from_spec(spec); spec.loader.exec_module(helper)
    helper.ROOT = ROOT
    helper.state = json.loads((ROOT / 'state.json').read_text())
    state = helper.state
    if state['status'] != 'waiting-build-authorization':
        raise RuntimeError('Campaign is not awaiting its first authorized build')
    RUN_OWNED = True
    state.update(status='building', dispatcherPid=os.getpid(), communicationRetries=communication_retries)
    helper.save()
    build(state)
    while True:
        previous = json.loads((PREVIOUS / 'state.json').read_text())
        if previous['status'] == 'complete':
            break
        time.sleep(300)
    with (PREVIOUS / 'campaign.lock').open('a') as previous_lock:
        fcntl.flock(previous_lock, fcntl.LOCK_EX)
        targets = previous['frequencyTargets']
        state['frequencyTargets'] = targets; state['status'] = 'testing'; helper.save()
        for round_number in range(1, 4):
            job = helper.test(CONFIG_NAME, round_number, WORK / 'artifact', targets)
            if job['status'] != 'complete':
                raise RuntimeError('Allocator-only test failed; evidence retained; no automatic panic retry')
            state['completed'] += 1; state['queuedRuns'].remove(round_number); helper.save()
        state['averages'] = {key: round(sum(j['scores'][key] for j in state['runs']) / 3, 2) for key in ('1', '16')}
        state['status'] = 'complete'; helper.save()


if __name__ == '__main__':
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--prepare', action='store_true')
    action.add_argument('--build-and-run', action='store_true', help='Only invoke after explicit build authorization')
    parser.add_argument('--communication-retries', type=int, choices=(0, 1), default=0,
                        help='separate power-cycle retry authorization; default disabled')
    args = parser.parse_args()
    ROOT.mkdir(parents=True, exist_ok=True)
    with (ROOT / 'campaign.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            prepare() if args.prepare else run(args.communication_retries)
        except Exception as exc:
            if args.build_and_run and RUN_OWNED and (ROOT / 'state.json').exists():
                state = json.loads((ROOT / 'state.json').read_text())
                state.update(status='blocked', current=None, failure=str(exc)); write_json(ROOT / 'state.json', state)
            print(type(exc).__name__ + ': ' + str(exc), file=sys.stderr)
            sys.exit(1)
