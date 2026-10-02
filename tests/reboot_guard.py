"""Hardware-free regression checks for owned runner monitoring and cancellation."""
import ast
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace

source = Path(__file__).resolve().parents[1] / 'scripts/real-k3-remote.py'
tree = ast.parse(source.read_text())

def load(name, env):
    node = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name)
    exec(compile(ast.Module(body=[node], type_ignores=[]), '<guard>', 'exec'), env)
    return env[name]

with tempfile.TemporaryDirectory() as directory:
    log = Path(directory) / 'console.log'
    status = Path(directory) / 'status.json'
    saved = []
    runner = SimpleNamespace(STATUS_PATH=status, now_iso=lambda: 'now',
                             write_status=lambda items: saved.extend(items))
    initial = {'bootId': 'first', 'release': 'test'}
    env = {'json': json, 'runner': runner, 'spec': {'runId': 'owned'},
           'original_drain': lambda child: None, 'log_path': log,
           'boot_count': 1, 'initial_identity': initial}
    guard = load('drain_with_reboot_guard', env)
    for console, identity, fails in [
        ('U-Boot SPL', initial, False),
        ('U-Boot SPL\nU-Boot SPL', initial, True),
        ('U-Boot SPL', {'bootId': 'second', 'release': 'test'}, True),
    ]:
        log.write_text(console)
        status.write_text(json.dumps({'runs': [{'run_id': 'owned', 'status': 'running'},
                                               {'run_id': 'other', 'status': 'running'}]}))
        env['board_identity'] = lambda: identity
        saved.clear()
        try:
            guard(None)
        except RuntimeError:
            assert fails
            assert saved[0]['status'] == 'failed'
            assert saved[1]['status'] == 'running'
        else:
            assert not fails
    env['stopping'] = False
    cancel = load('stop_owned_runner', env)
    saved.clear()
    try:
        cancel(15, None)
    except RuntimeError:
        pass
    assert saved[0]['status'] == 'failed' and saved[1]['status'] == 'running'
print('reboot, boot-id change and owned cancellation guards passed')
