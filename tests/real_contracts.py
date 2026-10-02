# Pure contract checks. Importing real-stage.py does not execute its main().
import ast
import base64
import contextlib
import importlib.util
import io
import json
import re
import shlex
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path
spec = importlib.util.spec_from_file_location('real_stage', Path(__file__).parent.parent / 'scripts/real-stage.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
assert m.UNIXBENCH_MONITOR_SECONDS == 80 * 60
argv = m.build_command('/toolchain/riscv64-unknown-linux-gnu-gcc', 8)
assert argv == ['make', '-j8', 'ARCH=riscv', 'CROSS_COMPILE=/toolchain/riscv64-unknown-linux-gnu-', 'KCONFIG_NOSILENTUPDATE=1', 'Image']
labels = ['Dhrystone 2', 'Double-Precision Whetstone', 'Execl Throughput', 'File Copy 1024', 'File Copy 256', 'File Copy 4096', 'Pipe Throughput', 'Pipe-based Context Switching', 'Process Creation', 'Shell Scripts (1 concurrent)', 'Shell Scripts (8 concurrent)', 'System Call Overhead']
stdout = ''.join(f'running {n} parallel {copies} of tests\n' + '\n'.join(labels) + '\nSystem Benchmarks Index Score 123.4\n' for n,copies in [(1,'copy'),(16,'copies')])
item = {'status':'complete','exit_rc':0,'anomalies':[]}
build = {'configHash':'config','buildId':'build','kernelRelease':'release'}
evidence = {'status':'success','exitCode':'0','stdout':stdout,'anomaly':'','raw':{'result':'raw fixture'},'configHash':'config','buildId':'build','release':'release'}
frequency = {'lock': {'ok': True, 'target_khz': 1200000},
             'restore': {'lock_state_intact': True, 'verified': True}}
checks,scores=m.judge_evidence(item,evidence,build,True,frequency)
assert all(checks.values()) and set(scores)=={'1','16'}
missing_frequency,_=m.judge_evidence(item,evidence,build,True,{})
assert not any(missing_frequency[key] for key in ('frequencyLocked','frequencyHeld','frequencyRestored'))
for key, value, failed in [('lock', {'ok': False}, 'frequencyLocked'),
                           ('restore', {'lock_state_intact': False, 'verified': True}, 'frequencyHeld'),
                           ('restore', {'lock_state_intact': True, 'verified': False}, 'frequencyRestored')]:
    result,_=m.judge_evidence(item,evidence,build,True,{**frequency,key:value})
    assert result[failed] is False
for key,value,failed in [('status','partial','status'),('exitCode','1','exitCode'),('stdout',stdout.split('running 16')[0],'scores'),('anomaly','Kernel panic','anomalies'),('raw',{},'rawResults'),('buildId','wrong','imageIdentity')]:
    result,_=m.judge_evidence(item,{**evidence,key:value},build,True,frequency)
    assert result[failed] is False
assert m.judge_evidence(item,evidence,build,False,frequency)[0]['cleanup'] is False

# Mock board sysfs contract: whitespace-separated CPU lists, per-policy targets and restore.
wrapper_path = Path(__file__).parent.parent/'scripts/real-k3-remote.py'
wrapper_ast = ast.parse(wrapper_path.read_text())
frequency_source = next(ast.literal_eval(n.value) for n in wrapper_ast.body
                        if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'FREQUENCY_SCRIPT' for t in n.targets))
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    (root/'cpufreq').mkdir(); (root/'run').mkdir(); (root/'online').write_text('0 1 2 3 4 5 6 7')
    for index, members, low, high, available in [(0, '0 1 2 3', 400000, 1200000, '400000 800000 1200000'),
                                                   (4, '4 5 6 7', 600000, 1200000, '600000 1000000 1200000')]:
        policy = root/'cpufreq'/f'policy{index}'; policy.mkdir()
        for name, value in {'related_cpus':members, 'scaling_governor':'powersave',
                            'scaling_min_freq':str(low), 'scaling_max_freq':str(high),
                            'scaling_available_governors':'performance powersave',
                            'scaling_available_frequencies':available,
                            'cpuinfo_min_freq':str(low), 'cpuinfo_max_freq':str(high)}.items():
            (policy/name).write_text(value)
    frequency_source = frequency_source.replace("Path('/sys/devices/system/cpu/cpufreq')", f"Path({str(root/'cpufreq')!r})")
    frequency_source = frequency_source.replace("'/sys/devices/system/cpu/online'", repr(str(root/'online')))
    frequency_source = frequency_source.replace("'/run/k3-agent-frequency-'", repr(str(root/'run/k3-agent-frequency-')))
    def run_board_frequency(action, saved=None, targets=None):
        request = {'action':action, 'runId':'contract-test', 'saved':saved or [], 'targets':targets or {}}
        source = frequency_source.replace('__REQUEST__', 'json.loads('+repr(json.dumps(request))+')')
        output = io.StringIO()
        with contextlib.redirect_stdout(output): exec(compile(source, '<mock-cpufreq>', 'exec'), {})
        return json.loads(output.getvalue().split('__K3_FREQ_RESULT__', 1)[1])
    locked = run_board_frequency('lock')
    assert locked['ok'] and sorted(locked['targets'].values()) == [1200000, 1200000]
    restored = run_board_frequency('restore', locked['saved'], locked['targets'])
    assert restored['verified'] and restored['lock_state_intact']
    policy4 = root/'cpufreq'/'policy4'
    (policy4/'scaling_max_freq').write_text('1600000')
    (policy4/'scaling_available_frequencies').write_text('600000 1000000 1600000')
    (policy4/'cpuinfo_max_freq').write_text('1600000')
    per_policy = run_board_frequency('lock')
    assert per_policy['ok'] and sorted(per_policy['targets'].values()) == [1200000, 1600000]
    restored = run_board_frequency('restore', per_policy['saved'], per_policy['targets'])
    assert restored['verified'] and restored['lock_state_intact']
    mismatch = run_board_frequency('lock', targets={str(root/'cpufreq'/'policy0'):1200000,
                                                       str(policy4):1200000})
    assert not mismatch['ok'] and mismatch['rollback']['verified']

# CPU online count is not benchmark affinity. Probe only a mocked proc/cgroup tree.
environment_source = next(ast.literal_eval(n.value) for n in wrapper_ast.body
                          if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'ENVIRONMENT_SCRIPT' for t in n.targets))
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory); proc = root/'proc'; group = root/'cgroup/session'; group.mkdir(parents=True)
    (proc/'self').mkdir(parents=True); (proc/'111').mkdir()
    (proc/'self/status').write_text('Cpus_allowed_list:\t0-7\n')
    (proc/'self/cgroup').write_text('0::/session\n')
    (proc/'111/status').write_text('Cpus_allowed_list:\t0-7\n')
    (proc/'111/cgroup').write_text('0::/session\n')
    (proc/'111/cmdline').write_bytes(b'/usr/bin/perl\0-w\0/home/test/UnixBench/Run\0')
    (group/'cpuset.cpus.effective').write_text('0-7\n'); (group/'cpu.max').write_text('max 100000\n')
    (root/'online').write_text('0-15\n')
    fixture = environment_source.replace("Path('/proc')", f"Path({str(proc)!r})")
    fixture = fixture.replace("'/proc/self/status'", repr(str(proc/'self/status')))
    fixture = fixture.replace("'/proc/self/cgroup'", repr(str(proc/'self/cgroup')))
    fixture = fixture.replace("Path('/sys/fs/cgroup')", f"Path({str(root/'cgroup')!r})")
    fixture = fixture.replace("'/sys/devices/system/cpu/online'", repr(str(root/'online')))
    env = {}; exec(compile(fixture, '<mock-cpu-environment>', 'exec'), env)
    snapshot = env['environment_snapshot']()
    assert snapshot['onlineCpus'] == '0-15' and snapshot['sshCpusAllowedList'] == '0-7'
    assert snapshot['cpusetEffective'] == '0-7' and snapshot['cpuMax'] == 'max 100000'
    assert snapshot['benchmarkProcesses'][0]['cpusAllowedList'] == '0-7'

# Mock SSH framing/credentials. The helper is sent to K3 via SSH, never run on the jump host.
request_fn = next(n for n in wrapper_ast.body if isinstance(n, ast.FunctionDef) and n.name == 'board_frequency_request')
class FakeMatch:
    def __init__(self, value=None): self.value = value
    def group(self, index): return self.value
class FakeChild:
    def __init__(self, states): self.states = list(states); self.sent = []; self.match = FakeMatch(); self.alive = True
    def expect(self, patterns, timeout):
        index, value = self.states.pop(0)
        self.match = FakeMatch(value)
        return index
    def sendline(self, text): self.sent.append(text)
    def isalive(self): return self.alive
    def close(self, force=False): self.alive = False
class FakePexpect:
    EOF = object()
    def __init__(self, child): self.child = child; self.argv = None
    def spawn(self, *args, **kwargs): self.argv = args; return self.child

def call_frequency(states):
    child = FakeChild(states); pexpect = FakePexpect(child)
    fake_runner = type('Runner', (), {'pexpect':pexpect, 'PASSWORD':'boardpw', 'SSH_OPTS':[],
                                      'USER':'bianbu', 'HOST':'192.168.137.200'})()
    env = {'json':json, 'base64':base64, 'zlib':zlib, 'shlex':shlex,
           'spec':{'runId':'contract-test'}, 'runner':fake_runner,
           'FREQUENCY_SCRIPT':'REQUEST = __REQUEST__'}
    ast.fix_missing_locations(ast.Module(body=[request_fn], type_ignores=[]))
    exec(compile(ast.Module(body=[request_fn], type_ignores=[]), '<mock-frequency-client>', 'exec'), env)
    result = env['board_frequency_request']('lock')
    return result, child, pexpect

frame_json = '{"ok":true,"target_khz":1200000}'
result, child, pexpect = call_frequency([(1,None),(0,None),(1,frame_json),(0,None)])
assert result == {'ok':True,'target_khz':1200000} and child.sent == ['boardpw','boardpw']
assert pexpect.argv[0] == '/usr/bin/ssh' and 'bianbu@192.168.137.200' in pexpect.argv[1]
result, child, _ = call_frequency([(2,frame_json),(0,None)])
assert result['ok'] and not child.sent
try:
    call_frequency([(0,None),(0,None)])
    raise AssertionError('sudo retry must fail closed')
except RuntimeError as error:
    assert 'authentication failed' in str(error)

# Signal cleanup regression with a fake runner; no SSH, serial or board operations.
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    (root/'owner').write_text('fixture-run')
    (root/'status.json').write_text('{"runs": []}')
    runner = root/'runner.py'
    runner.write_text('''import os,signal\nfrom pathlib import Path\nRUN_IDS=['fixture-run']\nSTATUS_PATH=Path(__file__).parent/'status.json'\ndef power(action):\n    (Path(__file__).parent/'power-action.txt').write_text(action)\ndef collect_result(run):\n    return {}\ndef main():\n    try:\n        os.kill(os.getpid(),signal.SIGTERM)\n        return 0\n    except RuntimeError:\n        return 1\n    finally:\n        power('off')\n''')
    wrapper = Path(__file__).parent.parent/'scripts/real-k3-remote.py'
    completed = subprocess.run([sys.executable, str(wrapper), json.dumps({'runner':str(runner),'lease':str(root),'runId':'fixture-run'})], capture_output=True, text=True, check=True)
    report = json.loads(completed.stdout.split('WORKFLOW_REPORT=',1)[1])
    assert report['exitCode'] == 1 and report['lastPowerAction'] == 'off'
    assert (root/'power-action.txt').read_text() == 'off'
    assert not (root/'runner.pid').exists()
