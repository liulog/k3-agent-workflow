"""Sent over SSH stdin by real-stage.py; wraps the installed runner without editing it."""
import importlib.util
import json
import os
from pathlib import Path
import shlex
import signal
import sys

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
    'configHash':hashlib.sha256(gzip.decompress(Path('/proc/config.gz').read_bytes())).hexdigest()}))
'''
original_collect = runner.collect_result

def collect(run_id):
    data = original_collect(run_id)
    code = 'RUN_ID=' + repr(run_id) + '\n' + board_source
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
    raise RuntimeError('Owned workflow runner interrupted by signal ' + str(signum))
for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
    signal.signal(sig, stop_owned_runner)
rc = runner.main()  # Existing runner owns boot, benchmark, graceful shutdown and relay-off.
payload = json.loads(runner.STATUS_PATH.read_text())
items = [x for x in payload.get('runs', []) if x.get('run_id') == spec['runId']]
item = items[0] if len(items) == 1 else {}
# Collect-result fields are nested by the installed runner; normalize for local validation.
item['workflowEvidence'] = item.get('collected', {}).get('workflowEvidence', {})
print('WORKFLOW_REPORT=' + json.dumps({'exitCode': rc, 'lastPowerAction': last_power, 'item': item}), flush=True)
(lease / 'runner.pid').unlink(missing_ok=True)
# Return a report even when the benchmark failed; the parent validates each criterion.
