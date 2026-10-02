"""Sent over SSH stdin by real-stage.py; wraps the installed runner without editing it."""
import base64
import importlib.util
import json
import os
from pathlib import Path
import shlex
import signal
import sys
import zlib

spec = json.loads(sys.argv[1])
lease = Path(spec['lease'])
if (lease / 'owner').read_text() != spec['runId']:
    raise RuntimeError('Board lease ownership mismatch')
loader = importlib.util.spec_from_file_location('owned_k3_runner', spec['runner'])
runner = importlib.util.module_from_spec(loader)
loader.loader.exec_module(runner)
if runner.RUN_IDS != [spec['runId']]:
    raise RuntimeError('Exactly one RUN_ID required')
(lease / 'runner.pid').write_text(str(os.getpid()))
last_power = None
original_power = runner.power

def power(action):
    global last_power
    original_power(action)
    last_power = action

runner.power = power

# Fixed sysfs operation executed on the K3 board after boot. The jump host only
# transports the request; no host cpufreq policy is read or changed.
ENVIRONMENT_SCRIPT = r'''
import json, os
from pathlib import Path

def environment_snapshot():
    def read(path):
        try: return Path(path).read_text().strip()
        except OSError: return None
    def allowed(path):
        return next((l.split(':', 1)[1].strip() for l in (read(path) or '').splitlines()
                     if l.startswith('Cpus_allowed_list:')), None)
    group = read('/proc/self/cgroup') or ''
    v2 = next((l[3:] for l in group.splitlines() if l.startswith('0::')), None)
    cg = Path('/sys/fs/cgroup') / v2.lstrip('/') if v2 is not None else None
    processes = []
    for proc in Path('/proc').glob('[0-9]*'):
        try:
            argv = (proc / 'cmdline').read_bytes().split(b'\0')
            executable = argv[0].decode(errors='replace')
            is_run = Path(executable).name == 'perl' and any(a.endswith(b'/UnixBench/Run') for a in argv)
            if not is_run and '/UnixBench/pgms/' not in executable: continue
            processes.append({'pid': int(proc.name), 'program': Path(executable).name,
                              'cpusAllowedList': allowed(proc / 'status'), 'cgroup': read(proc / 'cgroup')})
        except OSError: pass
        if len(processes) >= 128: break
    return {'onlineCpus': read('/sys/devices/system/cpu/online'),
            'sshCpusAllowedList': allowed('/proc/self/status'), 'cgroup': group,
            'cpusetEffective': read(cg / 'cpuset.cpus.effective') if cg else None,
            'cpuMax': read(cg / 'cpu.max') if cg else None, 'benchmarkProcesses': processes}
'''

FREQUENCY_REPORT = {"lock": None, "restore": None, "environment": []}
FREQUENCY_SAVED = None
FREQUENCY_LOCK_ATTEMPTED = False
FREQUENCY_RESTORE_ATTEMPTED = False
FREQUENCY_SCRIPT = r'''
import json, os, time
from pathlib import Path
REQUEST = __REQUEST__
RUN_ID = REQUEST['runId']
if not RUN_ID or any(not (c.isalnum() or c in '._-') for c in RUN_ID):
    raise RuntimeError('Invalid RUN_ID for frequency snapshot')
ROOT = Path('/sys/devices/system/cpu/cpufreq')
SNAPSHOT = Path('/run/k3-agent-frequency-' + RUN_ID + '.json')

def read(path): return Path(path).read_text().strip()
def write(path, value): Path(path).write_text(str(value))
def save_snapshot(saved, targets):
    temporary = SNAPSHOT.with_suffix('.tmp')
    temporary.write_text(json.dumps({'saved': saved, 'targets': targets}))
    temporary.chmod(0o600)
    temporary.replace(SNAPSHOT)
def cpus(text):
    result = set()
    for part in text.replace(',', ' ').split():
        if not part: continue
        bounds = part.split('-', 1)
        lo = int(bounds[0]); hi = int(bounds[1]) if len(bounds) == 2 else lo
        result.update(range(lo, hi + 1))
    return result

def current(policy):
    row = {'path': str(policy), 'governor': read(policy / 'scaling_governor'),
           'min_khz': int(read(policy / 'scaling_min_freq')),
           'max_khz': int(read(policy / 'scaling_max_freq'))}
    cur = policy / 'scaling_cur_freq'
    if cur.exists():
        try: row['cur_khz'] = int(read(cur))
        except Exception: row['cur_khz'] = None
    return row

def restore(saved):
    rows, errors = [], []
    for item in saved:
        p = Path(item['path'])
        try:
            cur_max = int(read(p / 'scaling_max_freq'))
            if item['min_khz'] > cur_max:
                write(p / 'scaling_max_freq', item['max_khz'])
                write(p / 'scaling_min_freq', item['min_khz'])
            else:
                write(p / 'scaling_min_freq', item['min_khz'])
                write(p / 'scaling_max_freq', item['max_khz'])
            write(p / 'scaling_governor', item['governor'])
            got = current(p)
            ok = (got['governor'] == item['governor'] and got['min_khz'] == item['min_khz'] and
                  got['max_khz'] == item['max_khz'])
            rows.append({'path': str(p), 'readback': got, 'verified': ok})
            if not ok: errors.append(str(p) + ': restore readback mismatch')
        except Exception as exc:
            rows.append({'path': str(p), 'error': str(exc), 'verified': False})
            errors.append(str(p) + ': ' + str(exc))
    return {'verified': bool(rows) and not errors, 'policies': rows, 'errors': errors}

def lock():
    online = cpus(read('/sys/devices/system/cpu/online'))
    policies = []
    for p in sorted(ROOT.glob('policy[0-9]*')):
        rel = p / 'related_cpus'
        if not rel.exists(): rel = p / 'affected_cpus'
        if rel.exists():
            members = cpus(read(rel)) & online
            if members: policies.append((p, sorted(members)))
    if not policies: raise RuntimeError('No online K3 cpufreq policies')
    if SNAPSHOT.exists(): raise RuntimeError('Frequency snapshot already exists for this RUN_ID')
    saved = []
    for p, members in policies:
        item = current(p); item['cpus'] = members; saved.append(item)
    save_snapshot(saved, {})
    try:
        for p, _ in policies:
            governors = p / 'scaling_available_governors'
            if governors.exists() and 'performance' not in read(governors).split():
                raise RuntimeError(str(p) + ' lacks performance governor')
            write(p / 'scaling_governor', 'performance')
        time.sleep(0.25)
        defaults = [int(read(p / 'scaling_max_freq')) for p, _ in policies]
        if any(value <= 0 for value in defaults): raise RuntimeError('Invalid performance-default frequency')
        targets = {str(p): default for (p, _), default in zip(policies, defaults)}
        expected = REQUEST.get('targets') or {}
        if expected and targets != expected:
            raise RuntimeError('K3 per-policy performance-default target vector changed from the matrix reference')
        save_snapshot(saved, targets)
        for (p, _), target in zip(policies, defaults):
            avail = p / 'scaling_available_frequencies'
            if avail.exists() and read(avail):
                if target not in [int(x) for x in read(avail).split()]:
                    raise RuntimeError(str(p) + ' lacks its performance-default frequency')
            else:
                if not (int(read(p / 'cpuinfo_min_freq')) <= target <= int(read(p / 'cpuinfo_max_freq'))):
                    raise RuntimeError(str(p) + ' rejects its performance-default frequency')
            write(p / 'scaling_min_freq', target)
            write(p / 'scaling_max_freq', target)
        time.sleep(0.25)
        locked = []
        for (p, members), default in zip(policies, defaults):
            got = current(p); got['cpus'] = members; got['performance_default_khz'] = default
            got['locked_khz'] = default; locked.append(got)
        verified = all(x['governor'] == 'performance' and x['min_khz'] == x['locked_khz'] and
                       x['max_khz'] == x['locked_khz'] for x in locked)
        if not verified: raise RuntimeError('K3 frequency lock readback mismatch')
        distinct = set(defaults)
        return {'ok': True, 'target_khz': defaults[0] if len(distinct) == 1 else None,
                'targets': targets, 'policies': locked, 'saved': saved}
    except Exception as exc:
        rollback = restore(saved)
        if rollback['verified']: SNAPSHOT.unlink(missing_ok=True)
        return {'ok': False, 'error': str(exc), 'saved': saved, 'rollback': rollback}

action = REQUEST['action']
if action == 'lock':
    try: result = lock()
    except Exception as exc: result = {'ok': False, 'error': str(exc), 'saved': []}
elif action == 'restore':
    saved = REQUEST.get('saved') or []; targets = REQUEST.get('targets') or {}
    if SNAPSHOT.exists():
        snapshot = json.loads(SNAPSHOT.read_text())
        if not saved: saved = snapshot.get('saved') or []
        if not targets: targets = snapshot.get('targets') or {}
    before, intact = [], bool(saved) and bool(targets)
    for item in saved:
        try:
            got = current(Path(item['path'])); before.append(got)
            target = int(targets.get(item['path']) or 0)
            got['locked_khz'] = target
            intact = intact and target > 0 and got['governor'] == 'performance' and got['min_khz'] == target and got['max_khz'] == target
        except Exception as exc:
            before.append({'path': item.get('path'), 'error': str(exc)}); intact = False
    restored = restore(saved)
    if restored['verified']: SNAPSHOT.unlink(missing_ok=True)
    result = {'ok': restored['verified'], 'verified': restored['verified'],
              'lock_state_intact': intact, 'before_restore': before, 'restore': restored}
else:
    result = {'ok': False, 'error': 'unknown action'}
print('__K3_FREQ_RESULT__' + json.dumps(result, separators=(',', ':')), flush=True)
'''

def board_frequency_request(action, saved=None, target_khz=None, targets=None):
    request = {'action': action, 'saved': saved or [], 'target_khz': target_khz,
               'targets': targets or {}, 'runId': spec['runId']}
    source = FREQUENCY_SCRIPT.replace('__REQUEST__', 'json.loads(' + repr(json.dumps(request)) + ')')
    encoded = base64.b64encode(zlib.compress(source.encode())).decode()
    pycode = "import base64,zlib;exec(compile(zlib.decompress(base64.b64decode('" + encoded + "')), '<k3-cpufreq>', 'exec'))"
    command = 'sudo -S python3 -c ' + shlex.quote(pycode)
    child = runner.pexpect.spawn('/usr/bin/ssh', runner.SSH_OPTS + [f'{runner.USER}@{runner.HOST}', command],
                                 encoding='utf-8', timeout=60)
    sudo_prompt = r'(?i)\[sudo[^\]]*\]\s*password(?:\s+for\s+[^:]*)?:'
    ssh_password = r'(?i)password:'
    frame = r'__K3_FREQ_RESULT__(\{[^\r\n]*\})'
    try:
        state = child.expect([sudo_prompt, ssh_password, frame, runner.pexpect.EOF], timeout=60)
        if state == 0:
            child.sendline(runner.PASSWORD)
            state = child.expect([sudo_prompt, frame, runner.pexpect.EOF], timeout=60)
            if state == 0: raise RuntimeError('K3 sudo authentication failed')
            if state == 1: payload = child.match.group(1)
            else: raise RuntimeError('K3 frequency helper exited without a result frame')
        elif state == 1:
            child.sendline(runner.PASSWORD)
            state = child.expect([sudo_prompt, frame, runner.pexpect.EOF], timeout=60)
            if state == 0:
                child.sendline(runner.PASSWORD)
                state = child.expect([sudo_prompt, frame, runner.pexpect.EOF], timeout=60)
                if state == 0: raise RuntimeError('K3 sudo authentication failed')
                if state == 1: payload = child.match.group(1)
                else: raise RuntimeError('K3 frequency helper exited without a result frame')
            elif state == 1: payload = child.match.group(1)
            else: raise RuntimeError('K3 frequency helper exited without a result frame')
        elif state == 2:
            payload = child.match.group(1)
        else:
            detail = (child.before or '').replace(runner.PASSWORD, '[REDACTED]')[-240:]
            raise RuntimeError('K3 SSH command exited without a result frame: ' + detail)
        child.expect(runner.pexpect.EOF, timeout=60)
        return json.loads(payload)
    finally:
        child.close(force=child.isalive())

def restore_board_frequency(child):
    global FREQUENCY_SAVED, FREQUENCY_RESTORE_ATTEMPTED
    if not FREQUENCY_LOCK_ATTEMPTED or FREQUENCY_RESTORE_ATTEMPTED: return
    FREQUENCY_RESTORE_ATTEMPTED = True
    try:
        lock_report = FREQUENCY_REPORT.get('lock') or {}
        report = board_frequency_request('restore', FREQUENCY_SAVED,
                                         lock_report.get('target_khz'), lock_report.get('targets'))
        FREQUENCY_REPORT['restore'] = report
        if report.get('verified'): FREQUENCY_SAVED = None
    except Exception as exc:
        FREQUENCY_REPORT['restore'] = {'verified': False, 'error': str(exc)}

original_run_passes = getattr(runner, 'run_passes', None)
if original_run_passes is not None:
    def run_passes_with_frequency(child, log_path):
        global FREQUENCY_SAVED, FREQUENCY_LOCK_ATTEMPTED
        FREQUENCY_LOCK_ATTEMPTED = True
        try:
            lock = board_frequency_request('lock', targets=spec.get('frequencyTargets'))
            FREQUENCY_REPORT['lock'] = lock
        except Exception as exc:
            FREQUENCY_REPORT['lock'] = {'ok': False, 'error': str(exc)}
            raise
        if not lock.get('ok'):
            rollback = lock.get('rollback', {})
            if rollback.get('verified'):
                FREQUENCY_REPORT['restore'] = {'verified': True, 'lock_state_intact': False,
                                               'source': 'lock-failure rollback', 'restore': rollback}
                global FREQUENCY_RESTORE_ATTEMPTED
                FREQUENCY_RESTORE_ATTEMPTED = True
            raise RuntimeError('K3 frequency lock failed; UnixBench not started: ' + str(lock.get('error', 'unverified')))
        FREQUENCY_SAVED = lock.get('saved') or None
        if not FREQUENCY_SAVED:
            raise RuntimeError('K3 frequency lock has no restore state; UnixBench not started')
        # Persist lock evidence immediately, not only after a multi-hour SSH call.
        frequency_path = runner.STATUS_PATH.with_suffix('.frequency.json')
        frequency_path.write_text(json.dumps(FREQUENCY_REPORT) + '\n')
        def board_identity():
            code = ENVIRONMENT_SCRIPT + "\nprint('__K3_BOOT__='+json.dumps({'release':os.uname().release,'bootId':Path('/proc/sys/kernel/random/boot_id').read_text().strip(),'environment':environment_snapshot()}))"
            _, output = runner.ssh_command('python3 -c ' + shlex.quote(code))
            frames = [line.split('=', 1)[1] for line in output.splitlines() if line.startswith('__K3_BOOT__=')]
            if len(frames) != 1:
                raise RuntimeError('Board identity unavailable; refusing unverified benchmark continuation')
            snapshot = json.loads(frames[0])
            FREQUENCY_REPORT['environment'].append(snapshot.pop('environment'))
            runner.STATUS_PATH.with_suffix('.environment.json').write_text(json.dumps(FREQUENCY_REPORT['environment']) + '\n')
            return snapshot
        initial_identity = board_identity()
        if spec.get('kernelRelease') and initial_identity['release'] != spec['kernelRelease']:
            raise RuntimeError('Wrong kernel release before UnixBench')
        # Inspect only at the runner's existing 80-minute monitoring cadence.
        # A reboot without panic text must not remain 'running' for eight hours.
        initial_log = log_path.read_text(encoding='utf-8', errors='replace')
        boot_count = initial_log.count('U-Boot SPL')
        original_drain = runner.drain_minicom
        def drain_with_reboot_guard(terminal):
            original_drain(terminal)
            text = log_path.read_text(encoding='utf-8', errors='replace')
            rebooted = text.count('U-Boot SPL') > boot_count
            identity_error = None
            if not rebooted:
                try:
                    rebooted = board_identity() != initial_identity
                except Exception as exc:
                    identity_error = str(exc)
            if rebooted or identity_error:
                payload = json.loads(runner.STATUS_PATH.read_text())
                for item in payload.get('runs', []):
                    if item.get('run_id') == spec['runId'] and item.get('status') == 'running':
                        item.update(status='failed', error=identity_error or 'Board rebooted during UnixBench',
                                    finished_at=runner.now_iso())
                runner.write_status(payload.get('runs', []))
                raise RuntimeError(identity_error or 'Board rebooted during UnixBench; no automatic retry')
        runner.drain_minicom = drain_with_reboot_guard
        try:
            return original_run_passes(child, log_path)
        finally:
            runner.drain_minicom = original_drain
    runner.run_passes = run_passes_with_frequency
    original_board_poweroff = runner.board_poweroff
    def board_poweroff_with_frequency_restore(child):
        try: restore_board_frequency(child)
        finally: original_board_poweroff(child)
    runner.board_poweroff = board_poweroff_with_frequency_restore
    original_close_minicom = runner.close_minicom
    def close_minicom_with_frequency_restore(child):
        try: restore_board_frequency(child)
        finally: original_close_minicom(child)
    runner.close_minicom = close_minicom_with_frequency_restore

# Board Python emits a single framed JSON object; no shell interpolation of RUN_ID.
board_source = r'''
import gzip,hashlib,json,os,struct
from pathlib import Path
root=Path('/home/bianbu/unixbench-runs') / RUN_ID / 'unixbench'
def read(name):
    p=root/name
    if p.stat().st_size > 4*1024*1024: raise RuntimeError('Evidence file too large')
    return p.read_text(errors='replace')
notes=Path('/sys/kernel/notes').read_bytes()
off=0; build_id=None
while off+12<=len(notes):
    ns,ds,kind=struct.unpack_from('<III',notes,off);off+=12
    name=notes[off:off+ns];off+=(ns+3)&~3
    desc=notes[off:off+ds];off+=(ds+3)&~3
    if kind==3 and name.rstrip(b'\0')==b'GNU':build_id=desc.hex()
raw={}
for p in sorted((root/'raw-results').glob('*')):
    if p.is_file() and p.stat().st_size <= 4*1024*1024:
        raw[p.name]=p.read_text(errors='replace')
print('WORKFLOW_EVIDENCE='+json.dumps({'status':read('status.txt'),'exitCode':read('exit-code.txt'),
    'stdout':read('stdout.txt'),'stderr':read('stderr.txt'),'anomaly':read('anomaly-scan.txt'),
    'raw':raw,'release':os.uname().release,'buildId':build_id,
    'configHash':hashlib.sha256(gzip.decompress(Path('/proc/config.gz').read_bytes())).hexdigest(),
    'environment':environment_snapshot()}))
'''
original_collect = runner.collect_result

def collect(run_id):
    data = original_collect(run_id)
    code = ENVIRONMENT_SCRIPT + '\nRUN_ID=' + repr(run_id) + '\n' + board_source
    try:
        _, output = runner.ssh_command('python3 -c ' + shlex.quote(code))
        framed = [s for s in output.splitlines() if s.startswith('WORKFLOW_EVIDENCE=')]
        if len(framed) != 1:
            raise RuntimeError('Missing unique evidence frame')
        data['workflowEvidence'] = json.loads(framed[0].split('=', 1)[1])
    except Exception:
        data['workflowEvidence'] = {'collectionError': 'Full board evidence unavailable before shutdown'}
    return data

runner.collect_result = collect
# Python's default SIGTERM skips finally. Give the owned runner a chance to close
# its terminal and power off instead. SIGKILL/host loss still require inspection.
stopping = False
def stop_owned_runner(signum, _frame):
    global stopping
    if stopping:
        return
    stopping = True
    if runner.STATUS_PATH.exists():
        payload = json.loads(runner.STATUS_PATH.read_text())
        for item in payload.get('runs', []):
            if item.get('run_id') == spec['runId'] and item.get('status') == 'running':
                item.update(status='failed', error='Owned runner cancelled by signal ' + str(signum),
                            finished_at=runner.now_iso())
        if payload.get('runs'):
            runner.write_status(payload['runs'])
    raise RuntimeError('Owned workflow runner interrupted by signal ' + str(signum))
for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
    signal.signal(sig, stop_owned_runner)
rc = runner.main()  # Existing runner owns boot, benchmark, graceful shutdown and relay-off.
payload = json.loads(runner.STATUS_PATH.read_text())
items = [x for x in payload.get('runs', []) if x.get('run_id') == spec['runId']]
item = items[0] if len(items) == 1 else {}
# Collect-result fields are nested by the installed runner; normalize for local validation.
item['workflowEvidence'] = item.get('collected', {}).get('workflowEvidence', {})
print('WORKFLOW_REPORT=' + json.dumps({'exitCode': rc, 'lastPowerAction': last_power, 'item': item,
                                      'frequency': FREQUENCY_REPORT}), flush=True)
(lease / 'runner.pid').unlink(missing_ok=True)
# Return a report even when the benchmark failed; the parent validates each criterion.
