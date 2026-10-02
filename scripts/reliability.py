"""Fixed, opt-in communication recovery. No hardware or build operations on import."""
import copy
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time
import uuid

NETWORK_WAIT_SECONDS = 300
MAX_COMMUNICATION_RETRIES = 1


class RecoveryRefused(RuntimeError):
    pass


class NetworkUnavailable(RuntimeError):
    """A read-only jump-host probe failed; waiting is safe, power is not implied."""


def failure_kind(error, checks=None):
    text = str(error)
    # Negative evidence takes precedence over a coincident SSH error.
    rules = [
        ('kernel', r'kernel panic|\boops\b|unable to handle kernel|\bBUG:|RCU stall|hung task'),
        ('reboot', r'board reboot|boot.?id.*chang|wrong kernel release'),
        ('authentication', r'authentication failed|permission denied|host key verification'),
        ('frequency', r'frequency|cpufreq'),
        ('resource', r'recovery refused|still active|serial ownership|lease.*mismatch|lease.*owner|preflight conflict'),
        ('identity', r'identity.*mismatch|image.*changed|hash.*mismatch'),
    ]
    for kind, pattern in rules:
        if re.search(pattern, text, re.I):
            return kind
    if checks and any(value is not True for value in checks.values()):
        return 'evidence'
    if re.search(r'Remote command failed \(255\)|SSH deadline exceeded', text, re.I):
        return 'communication'
    return 'unknown'


def retries_used(job):
    return max(int(job.get('communicationRecoveryAttempts', 0)), len(job.get('attempts') or []))


def retry_available(job, maximum):
    if maximum not in (0, MAX_COMMUNICATION_RETRIES):
        raise ValueError('Communication retry limit must be 0 or 1')
    return retries_used(job) < maximum


def reserve_recovery(job, maximum, persist):
    if not retry_available(job, maximum):
        raise RecoveryRefused('Recovery refused: communication retry budget exhausted')
    job['communicationRecoveryAttempts'] = retries_used(job) + 1
    job.setdefault('recoveryEvents', []).append({'event': 'power-cycle-reserved', 'at': time.time()})
    # Persist BEFORE the first power action. A crash must not reset the budget.
    persist()


def next_attempt(job, spec=None):
    history = job.setdefault('attempts', [])
    snapshot = copy.deepcopy({k: v for k, v in job.items() if k != 'attempts'})
    snapshot['status'] = 'failed'
    history.append(snapshot)
    job.update(id='exp-' + str(uuid.uuid4()), status='queued', scores=None, checks=None,
               frequency=None, exitCode=None, failure=None, failureKind=None, recoveryPrepared=False,
               directory=job['directory'] + '-communication-retry')
    if spec is not None:
        spec.update(id=job['id'], directory=job['directory'])
        spec.pop('recoverLeaseOwner', None)
        spec.pop('recoverFailureEvidencePath', None)
    return job


def recover_communication(job, k3_root, before_power, record=lambda event: None):
    """Never steals a lease, stops a runner, or treats missing SSH as power-off."""
    ctl = [sys.executable, str(Path(k3_root) / 'scripts/k3ctl.py')]
    power_started = False

    def call(*args):
        try:
            result = subprocess.run(ctl + list(args), cwd=k3_root, capture_output=True,
                                    text=True, timeout=90, check=False)
        except subprocess.TimeoutExpired:
            if not power_started:
                raise NetworkUnavailable('Jump-host probe timed out') from None
            raise RecoveryRefused('Recovery refused: power-cycle outcome uncertain') from None
        if result.returncode:
            if result.returncode == 255 and not power_started:
                if failure_kind(result.stdout + result.stderr) == 'authentication':
                    raise RecoveryRefused('Recovery refused: jump-host authentication failed')
                raise NetworkUnavailable('Jump-host transport unavailable')
            # Never interpolate stderr/credential-bearing argv into durable state.
            raise RecoveryRefused(f'Recovery refused: {args[0]} failed (exit {result.returncode})')
        return result.stdout + result.stderr

    def free_resources():
        if call('runners').strip() != '(无 runner/minicom 进程)':
            raise RecoveryRefused('Recovery refused: runner/terminal still active or unknown')
        # serial-owner includes sudo fuser output; any owner/error/extra output fails closed.
        if call('serial-owner').strip() != '无 minicom/picocom/screen 进程':
            raise RecoveryRefused('Recovery refused: root-visible serial ownership not proven free')
        status = call('status').replace('\r', '')
        match = re.search(r'\nBENCHMARK_PROCESSES\n(.*?)\nMEMORY\n', status, re.S)
        if 'board_ssh_failed=' in status or '\nKERNEL\n' not in status or not match:
            raise RecoveryRefused('Recovery refused: board task state unknown; SSH failure is not proof of power-off')
        if match.group(1).strip():
            raise RecoveryRefused('Recovery refused: board benchmark still active')

    free_resources()
    # Acquire an absent lease, or verify this failed attempt's own inactive lease.
    # The claim closes the absent-lease race with cooperating test executors.
    code = ("from pathlib import Path; p=Path('/tmp/k3-agent-workflow-board.lock'); "
            "absent=not p.exists(); "
            "p.mkdir(mode=0o700) if absent else None; "
            f"(p/'owner').write_text({job['id']!r}) if absent else None; "
            f"assert (p/'owner').read_text().strip()=={job['id']!r}; "
            "assert not (p/'runner.pid').exists()")
    call('jump', '--', 'python3 -c ' + shlex.quote(code))
    # Recheck after claiming the lease, immediately before reserving power.
    free_resources()
    before_power()
    power_started = True
    call('power', 'off'); record('relay-off-acknowledged')
    time.sleep(30)
    call('power', 'on'); record('relay-on-acknowledged')
    time.sleep(45)
    free_resources()
    release = ("from pathlib import Path; p=Path('/tmp/k3-agent-workflow-board.lock'); "
               f"assert (p/'owner').read_text().strip()=={job['id']!r}; "
               "assert not (p/'runner.pid').exists(); (p/'owner').unlink(); p.rmdir()")
    call('jump', '--', 'python3 -c ' + shlex.quote(release))
    record('cleanup-verified')


def recover_with_wait(job, maximum, k3_root, persist):
    if not retry_available(job, maximum):
        raise RecoveryRefused('Recovery refused: communication retry budget exhausted')
    def record(event):
        job.setdefault('recoveryEvents', []).append({'event': event, 'at': time.time()})
        persist()
    while True:
        try:
            recover_communication(job, k3_root,
                                  lambda: reserve_recovery(job, maximum, persist), record)
            job['recoveryPrepared'] = True
            persist()
            return
        except NetworkUnavailable:
            # No power action has started. Do not spend the hardware retry budget
            # or generate new RUN_IDs merely because the network is unavailable.
            print('Network unavailable; waiting five minutes without power/lease takeover', flush=True)
            time.sleep(NETWORK_WAIT_SECONDS)


def execute_test(job, spec, repo, k3_root, persist, maximum=0):
    """Run the fixed one-shot stage; at most one separately owned communication retry."""
    if maximum not in (0, MAX_COMMUNICATION_RETRIES):
        raise ValueError('Communication retry limit must be 0 or 1')
    while True:
        directory = Path(job['directory'])
        with (directory / 'stage.log').open('ab', buffering=0) as log:
            completed = subprocess.run([sys.executable, str(Path(repo) / 'scripts/real-stage.py'),
                                        str(directory / 'spec.json')], cwd=repo,
                                       stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
        failure_path = directory / 'failure.json'
        result_path = directory / 'result.json'
        error = json.loads(failure_path.read_text()).get('error', '') if failure_path.exists() else ''
        result = json.loads(result_path.read_text()) if result_path.exists() else {}
        kind = failure_kind(error, result.get('checks'))
        if completed.returncode == 0 or kind != 'communication' or not retry_available(job, maximum):
            return completed
        job.update(status='recovering', failure=error, failureKind=kind, exitCode=completed.returncode)
        persist()
        recover_with_wait(job, maximum, k3_root, persist)
        next_attempt(job, spec)
        directory = Path(job['directory']); directory.mkdir(parents=True, exist_ok=False)
        (directory / 'spec.json').write_text(json.dumps(spec, indent=2) + '\n')
        job['status'] = 'running'
        persist()
        print('Communication recovery verified; one new attempt:', job['id'], flush=True)
