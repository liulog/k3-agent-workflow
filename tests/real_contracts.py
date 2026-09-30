# Pure contract checks. Importing real-stage.py does not execute its main().
import importlib.util
from pathlib import Path
spec = importlib.util.spec_from_file_location('real_stage', Path(__file__).parent.parent / 'scripts/real-stage.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
argv = m.build_command('/toolchain/riscv64-unknown-linux-gnu-gcc', 8)
assert argv == ['make', '-j8', 'ARCH=riscv', 'CROSS_COMPILE=/toolchain/riscv64-unknown-linux-gnu-', 'KCONFIG_NOSILENTUPDATE=1', 'Image']
labels = ['Dhrystone 2', 'Double-Precision Whetstone', 'Execl Throughput', 'File Copy 1024', 'File Copy 256', 'File Copy 4096', 'Pipe Throughput', 'Pipe-based Context Switching', 'Process Creation', 'Shell Scripts (1 concurrent)', 'Shell Scripts (8 concurrent)', 'System Call Overhead']
stdout = ''.join(f'running {n} parallel {copies} of tests\n' + '\n'.join(labels) + '\nSystem Benchmarks Index Score 123.4\n' for n,copies in [(1,'copy'),(16,'copies')])
item = {'status':'complete','exit_rc':0,'anomalies':[]}
build = {'configHash':'config','buildId':'build','kernelRelease':'release'}
evidence = {'status':'success','exitCode':'0','stdout':stdout,'anomaly':'','raw':{'result':'raw fixture'},'configHash':'config','buildId':'build','release':'release'}
checks,scores=m.judge_evidence(item,evidence,build,True)
assert all(checks.values()) and set(scores)=={'1','16'}
for key,value,failed in [('status','partial','status'),('exitCode','1','exitCode'),('stdout',stdout.split('running 16')[0],'scores'),('anomaly','Kernel panic','anomalies'),('raw',{},'rawResults'),('buildId','wrong','imageIdentity')]:
    result,_=m.judge_evidence(item,{**evidence,key:value},build,True)
    assert result[failed] is False
assert m.judge_evidence(item,evidence,build,False)[0]['cleanup'] is False

# Signal cleanup regression with a fake runner; no SSH, serial or board operations.
import json, subprocess, sys, tempfile
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
