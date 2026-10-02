#!/usr/bin/env python3
"""Fixed one-shot execution, invoked only by the explicitly enabled RealWorker.
No model text is ever interpreted as a command. No clean/defconfig/install/retries.
"""
import hashlib
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys


# The observed UnixBench run takes about 57 minutes. Avoid the previous
# minute-by-minute runner checks during the load; this delays anomaly detection
# and runner-managed shutdown by up to one monitor interval if a run hangs.
UNIXBENCH_MONITOR_SECONDS = 80 * 60


def digest(data):
    return hashlib.sha256(data).hexdigest()


def file_hash(path):
    with open(path, 'rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def save(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2))


def build_command(compiler, jobs):
    return ['make', f'-j{jobs}', 'ARCH=riscv', f'CROSS_COMPILE={compiler[:-3]}',
            'KCONFIG_NOSILENTUPDATE=1', 'Image']


def source_state(repo):
    def git(*args):
        return subprocess.check_output(['git', '--no-pager', *args], cwd=repo)
    # Git diff covers tracked source modifications, including staged changes.
    # Record all untracked source-like files; pre-existing Image copies are not build inputs.
    untracked = {}
    for p in git('ls-files', '--others', '--exclude-standard', '-z').split(b'\0'):
        if not p:
            continue
        name = os.fsdecode(p)
        if Path(name).suffix in {'.c', '.h', '.S', '.s', '.lds', '.sh', '.rs', '.py'} or Path(name).name in {'Makefile', 'Kconfig'}:
            untracked[name] = file_hash(repo / name)
    return {'head': git('rev-parse', 'HEAD').decode().strip(),
            'diffHash': digest(git('diff', '--no-ext-diff', '--no-textconv', '--binary', 'HEAD', '--')),
            'untrackedInputs': untracked}


def build(spec):
    repo, out = Path(spec['linuxRepo']), Path(spec['directory'])
    config = repo / '.config'
    if not config.is_file() or config.is_symlink():
        raise RuntimeError('Existing regular .config required; will not generate one')
    compiler = shutil.which('riscv64-unknown-linux-gnu-gcc')
    if not compiler:
        raise RuntimeError('Existing riscv64-unknown-linux-gnu-gcc is not on PATH; no installation attempted')
    before = config.read_bytes()
    (out / 'config.before').write_bytes(before)
    source = source_state(repo)
    save(out / 'source-before.json', source)
    command = build_command(compiler, min(8, os.cpu_count() or 1))
    save(out / 'command.json', {'argv': command, 'cwd': str(repo), 'configHash': digest(before)})
    print('Building existing configuration; no clean or defconfig', flush=True)
    env = dict(os.environ)
    for key in ('KBUILD_OUTPUT', 'KCONFIG_CONFIG', 'KBUILD_SRC', 'MAKEFLAGS', 'MAKEFILES', 'MFLAGS', 'KCONFIG_ALLCONFIG'):
        env.pop(key, None)
    env['KCONFIG_NOSILENTUPDATE'] = '1'
    with open(out / 'make.log', 'xb') as log:
        completed = subprocess.run(command, cwd=repo, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                   env=env, timeout=3600)
    unchanged = config.read_bytes() == before
    if not unchanged:
        raise RuntimeError('Configuration changed during make; stopped before testing. Original preserved in build/config.before; not silently restored')
    if completed.returncode:
        tail = (out / 'make.log').read_text(errors='replace')[-5000:]
        raise RuntimeError(f'make Image failed (exit {completed.returncode}), .config unchanged.\n{tail}')
    if source_state(repo) != source:
        raise RuntimeError('Source identity changed during build; Image will not be tested')
    image = repo / 'arch/riscv/boot/Image'
    if not image.is_file() or image.is_symlink() or image.stat().st_size == 0:
        raise RuntimeError('Successful make did not produce a regular nonempty Image')
    notes = subprocess.check_output([compiler[:-3] + 'readelf', '-n', str(repo / 'vmlinux')], text=True)
    match = re.search(r'Build ID:\s*([0-9a-f]+)', notes)
    if not match:
        raise RuntimeError('Kernel Build ID unavailable; refusing an unverifiable board handoff')
    shutil.copyfile(image, out / 'Image')
    (out / 'Image').chmod(0o400)
    report = {'mode': 'real', 'simulated': False, 'kind': 'build', 'experimentId': spec['id'],
              'sourceHash': spec['sourceHash'], 'buildExecuted': True, 'boardAccessed': False,
              'artifactHash': file_hash(out / 'Image'), 'configHash': digest(before),
              'configUnchanged': True, 'sourceUnchanged': True, 'source': source,
              'buildId': match.group(1), 'kernelRelease': (repo / 'include/config/kernel.release').read_text().strip()}
    save(out / 'result.json', report)
    print('BUILD VERIFIED:', report['artifactHash'], flush=True)


def judge_evidence(item, evidence, build_report, cleanup, frequency):
    stdout = evidence.get('stdout', '')
    # A complete default run must contain both 1-copy and 16-copy score tables.
    blocks = re.split(r'running\s+(\d+)\s+parallel\s+cop(?:y|ies)\s+of\s+tests', stdout, flags=re.I)
    scores = {}
    labels = ['Dhrystone 2', 'Double-Precision Whetstone', 'Execl Throughput',
              'File Copy 1024', 'File Copy 256', 'File Copy 4096', 'Pipe Throughput',
              'Pipe-based Context Switching', 'Process Creation', 'Shell Scripts (1 concurrent)',
              'Shell Scripts (8 concurrent)', 'System Call Overhead']
    for i in range(1, len(blocks) - 1, 2):
        score = re.search(r'System Benchmarks Index Score\s+([0-9.]+)', blocks[i + 1])
        if score and all(label in blocks[i + 1] for label in labels):
            scores[blocks[i]] = float(score.group(1))
    anomalies = evidence.get('anomaly', '')
    frequency_lock = frequency.get('lock') or {}
    frequency_restore = frequency.get('restore') or {}
    bad = r'Kernel panic|Oops|Unable to handle kernel|BUG:|WARNING:|page fault|access fault|RCU stall|hung task|stack smashing detected'
    return {'status': item.get('status') == 'complete' and evidence.get('status', '').strip() == 'success',
            'exitCode': item.get('exit_rc') == 0 and evidence.get('exitCode', '').strip() == '0',
            'scores': all(scores.get(c, 0) > 0 for c in ('1', '16')),
            'anomalies': 'anomaly' in evidence and not item.get('anomalies') and not re.search(bad, anomalies),
            'rawResults': bool(evidence.get('raw')) and all(evidence['raw'].values()),
            'imageIdentity': evidence.get('configHash') == build_report['configHash'] and evidence.get('buildId') == build_report['buildId'] and evidence.get('release') == build_report['kernelRelease'],
            'cleanup': cleanup,
            'frequencyLocked': frequency_lock.get('ok') is True,
            'frequencyHeld': frequency_restore.get('lock_state_intact') is True,
            'frequencyRestored': frequency_restore.get('verified') is True}, scores


def board_test(spec, redact):
    root, out = Path(spec['k3Root']), Path(spec['directory'])
    image = Path(spec['artifact']['path'])
    if file_hash(image) != spec['artifact']['sha256']:
        raise RuntimeError('Image changed before board handoff')
    build_report = json.loads((image.parent / 'result.json').read_text())
    module_spec = importlib.util.spec_from_file_location('k3ctl', root / 'scripts/k3ctl.py')
    k3 = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(k3)
    cfg = k3.Config(k3.load_toml(root / 'config/k3-auto.toml'), root / 'config/k3-auto.toml',
                    k3.load_toml(root / 'config/jump.toml'), root / 'config/jump.toml')
    secrets = sorted({s for s in [cfg.jump_password, cfg.jump_sudo_password, cfg.board_password] if s}, key=len, reverse=True)
    redact.extend(secrets)
    def masked(text):
        for s in secrets:
            text = text.replace(shlex.quote(s), '[REDACTED]').replace(s, '[REDACTED]')
        return text
    def masked_tree(value):
        if isinstance(value, str): return masked(value)
        if isinstance(value, list): return [masked_tree(item) for item in value]
        if isinstance(value, dict): return {key: masked_tree(item) for key, item in value.items()}
        return value
    class Lab(k3.Lab):
        timeout = 60
        def _ssh_prefix(self, *args, **kwargs):
            argv, env = super()._ssh_prefix(*args, **kwargs)
            argv[-1:-1] = ['-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=3']
            return argv, env
        def _exec(self, argv, env=None, stdin=None, check=True):
            try:
                p = subprocess.run(argv, env={**os.environ, **(env or {})}, input=stdin.encode() if stdin else None,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=self.timeout)
            except subprocess.TimeoutExpired:
                raise RuntimeError('SSH deadline exceeded: remote state UNKNOWN, lease retained; inspect manually') from None
            with open(out / 'remote.log', 'a') as f:
                f.write(masked(p.stdout.decode(errors='replace') + p.stderr.decode(errors='replace')))
            if check and p.returncode:
                raise RuntimeError(f'Remote command failed ({p.returncode}); inspect redacted test/remote.log')
            return p
    lab = Lab(cfg)
    skill_files = [root / 'skills' / name / 'SKILL.md' for name in ['k3-benchmark', 'k3-lab', 'k3-status']]
    save(out / 'skills.json', [{'path': str(p), 'sha256': file_hash(p)} for p in skill_files])
    # Cooperative lock is intentionally never stolen; tools outside this workflow may not honor it.
    lease = '/tmp/k3-agent-workflow-board.lock'
    q = shlex.quote
    previous_owner = spec.get('recoverLeaseOwner')
    if previous_owner:
        # Recover a named terminal run only. A failed prior attempt needs a local
        # report proving lock rollback, no benchmark start, and relay-off invocation.
        recovery_failure_path = spec.get('recoverFailureEvidencePath')
        allow_terminal_failure = False
        if recovery_failure_path:
            recovery_evidence = Path(recovery_failure_path)
            if not recovery_evidence.is_file() or recovery_evidence.is_symlink():
                raise RuntimeError('Recovery evidence is missing or unsafe')
            recovery_text = recovery_evidence.read_text(errors='replace')
            required = ['WORKFLOW_REPORT=', '"lastPowerAction": "off"',
                        '"rollback": {"verified": true', '"restore": {"verified": true',
                        'K3 frequency lock failed; UnixBench not started']
            if any(marker not in recovery_text for marker in required) or '__UNIXBENCH_DONE__' in recovery_text:
                raise RuntimeError('Prior failure evidence does not prove pre-benchmark rollback and relay-off')
            allow_terminal_failure = True
        status_path = f'{cfg.log_dir.rstrip("/")}/{previous_owner}.json'
        status_check = (
            "import json,sys,os; run_id,allow=sys.argv[2:4]; "
            "d=json.load(open(sys.argv[1])) if os.path.exists(sys.argv[1]) else {}; "
            "rs=[r for r in d.get('runs',[]) if r.get('run_id')==run_id]; "
            "r=rs[0] if len(rs)==1 else {}; c=r.get('collected') or {}; "
            "w=c.get('workflowEvidence') or {}; "
            "success=(r.get('status')=='complete' and r.get('exit_rc')==0 and bool(r.get('finished_at')) "
            "and not r.get('anomalies') and c.get('status')=='success' and c.get('benchmark_exit_code')==0 "
            "and w.get('status','').strip()=='success' and str(w.get('exitCode','')).strip()=='0' "
            "and bool(w.get('buildId')) and bool(w.get('configHash')) and bool(w.get('release'))); "
            "failed=(allow=='1' and (not rs or (r.get('status') in ('failed','complete') and bool(r.get('finished_at'))))); "
            "sys.exit(0 if success or failed else 1)"
        )
        lab.jump(f'test "$(cat {q(lease)}/owner)" = {q(previous_owner)} || exit 78\n'
                 f'python3 -c {q(status_check)} {q(status_path)} {q(previous_owner)} {1 if allow_terminal_failure else 0} || exit 80\n'
                 f'if test -e {q(lease)}/runner.pid; then '
                 f'pid=$(cat {q(lease)}/runner.pid); case "$pid" in ""|*[!0-9]*) exit 79;; esac; '
                 'test "$pid" -gt 1 || exit 79; kill -0 "$pid" 2>/dev/null && exit 79; '
                 f'rm -- {q(lease)}/runner.pid; fi\n'
                 "ps -eo comm=,args= | awk '$1 ~ /^(minicom|picocom|screen)$/ || ($1 ~ /python/ && $0 ~ /k3_(unixbench|lmbench|boot)/) {found=1} END {exit found ? 1 : 0}' || exit 75\n"
                 f"ps -eo pid=,comm=,args= | awk -v id={q(previous_owner)} '$0 ~ id && $0 !~ /awk/ {{found=1}} END {{exit found ? 1 : 0}}' || exit 75\n"
                 'command -v fuser >/dev/null || exit 76\n'
                 + 'owners=$(' + lab._sudo('fuser ' + q(cfg.serial_debug)) + ' 2>&1); rc=$?\n'
                 + 'test "$rc" = 1 && test -z "$owners" || exit 77\n')
        lab.jump(f'test "$(cat {q(lease)}/owner)" = {q(previous_owner)} && '
                 f'test ! -e {q(lease)}/runner.pid && rm {q(lease)}/owner && rmdir {q(lease)}')
    lab.jump(f'mkdir {q(lease)} || exit 73\nprintf %s {q(spec["id"])} > {q(lease)}/owner\n')
    safe_release = False
    try:
        # Require accessible serial devices, no existing runner/terminal and root-visible free serial.
        lab.jump(f'test -c {q(cfg.serial_relay)} && test -c {q(cfg.serial_debug)} && '
                 f'test -f {q(cfg.unixbench_py)} && test -x {q(lab.abs_script("power_sh"))} || exit 74\n'
                 "ps -eo comm=,args= | awk '$1 ~ /^(minicom|picocom|screen)$/ || ($1 ~ /python/ && $0 ~ /k3_(unixbench|lmbench|boot)/) {found=1} END {exit found ? 1 : 0}' || exit 75\n"
                 + 'command -v fuser >/dev/null || exit 76\n'
                 + 'owners=$(' + lab._sudo('fuser ' + q(cfg.serial_debug)) + ' 2>&1); rc=$?\n'
                 + 'test "$rc" = 1 && test -z "$owners" || exit 77\n')
        # Preserve the preflight evidence without treating failed SSH as proof of power-off.
        status = lab.status().stdout.decode(errors='replace').replace('\r', '')
        processes = re.search(r'\nBENCHMARK_PROCESSES\n(.*?)\nMEMORY\n', status, re.S)
        if processes and processes.group(1).strip():
            raise RuntimeError('Board benchmark processes detected; refusing to reboot or stop another task')
        name = 'Image-' + spec['id']
        lab.tftp_put(str(image), name)
        remote_image = cfg.tftp_root.rstrip('/') + '/' + name
        sha = lab.jump('sha256sum -- ' + q(remote_image)).stdout.decode().split()[0]
        if sha != spec['artifact']['sha256']:
            raise RuntimeError('TFTP upload SHA-256 mismatch')
        # Wrap, do not modify, the existing k3-auto runner. Collect full evidence BEFORE its poweroff.
        wrapper = Path(__file__).with_name('real-k3-remote.py').read_text()
        args = json.dumps({'runner': cfg.unixbench_py, 'runId': spec['id'], 'lease': lease,
                           'frequencyTargets': spec.get('frequencyTargets'),
                           'kernelRelease': build_report['kernelRelease']})
        lab.timeout = 9 * 3600
        result = lab.jump('python3 - ' + q(args) + " <<'WORKFLOW_REMOTE'\n" + wrapper + '\nWORKFLOW_REMOTE\n', env={
            'K3_IMAGE': name, 'K3_RUN_IDS': spec['id'], 'K3_LOG_PREFIX': spec['id'],
            'K3_STATUS_PATH': cfg.log_dir.rstrip('/') + '/' + spec['id'] + '.json',
            'K3_LOG_DIR': cfg.log_dir, 'K3_MONITOR_SECONDS': str(UNIXBENCH_MONITOR_SECONDS), 'TERM': 'xterm'})
        lab.timeout = 60
        lines = result.stdout.decode(errors='replace').splitlines()
        report = json.loads(next(line[len('WORKFLOW_REPORT='):] for line in reversed(lines) if line.startswith('WORKFLOW_REPORT=')))
        save(out / 'evidence.json', masked_tree(report))
        # Exit of our runner and acknowledged relay-off are necessary, but not sufficient: recheck serial.
        cleanup = report.get('exitCode') == 0 and report.get('lastPowerAction') == 'off'
        if cleanup:
            lab.jump('owners=$(' + lab._sudo('fuser ' + q(cfg.serial_debug)) + ' 2>&1); rc=$?\n'
                     'test "$rc" = 1 && test -z "$owners" || exit 77\n')
            safe_release = True
        item = report.get('item', {})
        frequency = report.get('frequency', {})
        checks, scores = judge_evidence(item, item.get('workflowEvidence', {}), build_report, cleanup, frequency)
        final = {'mode': 'real', 'simulated': False, 'kind': 'test', 'experimentId': spec['id'],
                 'sourceHash': spec['sourceHash'], 'boardAccessed': True, 'benchmark': 'unixbench',
                 'artifactHash': sha, 'checks': checks, 'scores': scores,
                 'frequency': frequency, 'evidence': str(out / 'evidence.json'), 'relayOffCommandAcknowledged': cleanup}
        save(out / 'result.json', final)
        if not all(checks.values()):
            raise RuntimeError('UnixBench evidence incomplete/failed: ' + ', '.join(k for k,v in checks.items() if not v))
    finally:
        if safe_release:
            lab.jump(f'test "$(cat {q(lease)}/owner)" = {q(spec["id"])} && rm {q(lease)}/owner && rmdir {q(lease)}')
        else:
            print('Remote lease retained; hardware state needs operator attention. No blind poweroff/kill attempted.', flush=True)


def main():
    os.umask(0o077)
    spec = json.loads(Path(sys.argv[1]).read_text())
    redact = []
    try:
        if spec['stage'] == 'build':
            git_dir = subprocess.check_output(['git', 'rev-parse', '--absolute-git-dir'], cwd=spec['linuxRepo'], text=True).strip()
            with open(Path(git_dir) / 'k3-workflow-build.lock', 'a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                build(spec)
        elif spec['stage'] == 'test':
            board_test(spec, redact)
        else:
            raise RuntimeError('Unknown stage')
    except Exception as exc:
        message = str(exc)
        for s in redact:
            message = message.replace(shlex.quote(s), '[REDACTED]').replace(s, '[REDACTED]')
        save(Path(spec['directory']) / 'failure.json', {'error': message})
        print(message, file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
