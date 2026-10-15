"""Single-worker runtime. No model, compiler, shell or board imports.

Adapters receive frozen data and return receipts. Unknown state holds the queue.
The pilot accepts plan/demo only; a real resource validator is not implemented.
"""
from contextlib import AbstractContextManager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import time
import uuid

from .contract import API, ContractError, canonical, digest, submit, outcome, receipt


def filehash(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def atomic(path, value):
    temporary = path.with_name(path.name + '.' + str(uuid.uuid4()) + '.tmp')
    with temporary.open('xb') as stream:
        stream.write(canonical(value) + b'\n')
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)
    descriptor = os.open(str(path.parent), os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class Core(AbstractContextManager):
    def __init__(self, root, adapters=None, allow_demo_retry=False):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = (self.root / 'core.lock').open('a')
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.adapters = adapters or {}
            self.allow_demo_retry = allow_demo_retry
            self.path = self.root / 'state.json'
            if self.path.is_symlink():
                raise ContractError('State must be an ordinary local file')
            self.state = json.loads(self.path.read_text()) if self.path.exists() else {
                'apiVersion': API, 'paused': False, 'batches': [], 'events': []}
            if self.state.get('apiVersion') != API:
                raise ContractError('State version mismatch')
            interrupted = False
            for batch in self.state['batches']:
                for task in batch['tasks']:
                    if task['status'] == 'running':
                        task.update(status='needs_attention', error='Interrupted task; never automatically replay')
                        batch['status'] = 'needs_attention'
                        self.event('restart.needs_attention', task['id'])
                        interrupted = True
            if interrupted:
                self.save()
        except Exception:
            self.lock.close()
            raise

    def __exit__(self, *args):
        self.lock.close()

    def event(self, kind, task=None):
        self.state['events'].append({'sequence': len(self.state['events']) + 1,
                                     'type': kind, 'taskId': task, 'at': time.time()})

    def save(self):
        atomic(self.path, self.state)

    def batch(self, key):
        batch = next((b for b in self.state['batches'] if b['key'] == key), None)
        if batch is None:
            raise ContractError('Batch not found')
        return batch

    def submit(self, request):
        request = submit(request)
        key, request_hash = request['key'], digest(request)
        old = next((b for b in self.state['batches'] if b['key'] == key), None)
        if old:
            if old['requestHash'] != request_hash:
                raise ContractError('Idempotency key reused with different request')
            return old
        if request['mode'] == 'demo':
            if request['retryLimit'] and not self.allow_demo_retry:
                raise ContractError('Retry requires explicit offline operator flag, not JSON alone')
            for job in request['jobs']:
                if job['adapter'] not in self.adapters:
                    raise ContractError('Unregistered operator adapter')
        tasks = []
        for job in request['jobs']:
            binding = self.adapters.get(job['adapter'])
            tasks.append({'id': 'task-' + str(uuid.uuid4()), 'jobKey': job['key'],
                          'job': job, 'attempt': 1, 'status': 'queued',
                          'adapterFingerprint': binding.fingerprint if request['mode'] == 'demo' else None})
        directory = self.root / 'batches' / key
        directory.mkdir(parents=True, exist_ok=False, mode=0o700)
        atomic(directory / 'request.json', request)
        batch = {'key': key, 'requestHash': request_hash,
                 'requestFileHash': filehash(directory / 'request.json'),
                 'mode': request['mode'], 'retryLimit': request['retryLimit'],
                 'retryMode': request['retryMode'], 'retryUsed': 0, 'status': 'queued', 'tasks': tasks}
        self.state['batches'].append(batch)
        self.event('batch.queued')
        self.save()
        return batch

    def snapshot(self, batch):
        path = self.root / 'batches' / batch['key'] / 'request.json'
        if (path.is_symlink() or not path.is_file() or path.stat().st_size > 256 * 1024
                or filehash(path) != batch['requestFileHash']):
            raise ContractError('Immutable request snapshot changed')
        request = json.loads(path.read_text())
        if (digest(request) != batch['requestHash'] or any(
                batch[key] != request[key] for key in ('mode', 'retryLimit', 'retryMode'))):
            raise ContractError('Request identity/policy changed')
        for task in batch['tasks']:
            if task['job'] not in request['jobs']:
                raise ContractError('Task differs from frozen input')
        return request

    def tick(self):
        if self.state['paused']:
            return {'status': 'paused'}
        # A held/unknown batch is a global barrier; never dispatch a later batch.
        batch = next((b for b in self.state['batches'] if b['status'] not in ('succeeded', 'cancelled')), None)
        if batch is None:
            return {'status': 'idle'}
        if batch['status'] == 'needs_attention':
            return {'status': 'needs_attention', 'key': batch['key']}
        task = next(t for t in batch['tasks'] if t['status'] == 'queued')
        context = {'apiVersion': API, 'taskId': task['id'], 'requestHash': batch['requestHash'],
                   'attempt': task['attempt'], 'mode': batch['mode'], 'jobKey': task['jobKey'],
                   'kind': task['job']['kind'], 'params': task['job']['params']}
        directory = self.root / 'tasks' / task['id']
        task['status'] = batch['status'] = 'running'
        self.event('task.started', task['id'])
        self.save()  # Persist before creating attempt files or invoking a worker.
        try:
            directory.mkdir(parents=True, exist_ok=False, mode=0o700)
            self.snapshot(batch)
            if batch['mode'] == 'plan':
                result = receipt(context, data={'plannedJob': task['job'],
                                               'commandsExecuted': False, 'boardAccessed': False})
            else:
                adapter = self.adapters[task['job']['adapter']]
                if adapter.fingerprint != task['adapterFingerprint']:
                    raise ContractError('Registered adapter identity changed')
                result = adapter.run(context)
                if adapter.fingerprint != task['adapterFingerprint']:
                    raise ContractError('Adapter entrypoint changed during execution')
            self.snapshot(batch)
            result = outcome(result, context)
            atomic(directory / 'result.json', result)
            task['resultPath'] = str(directory / 'result.json')
            task['resultHash'] = filehash(directory / 'result.json')
            if result['status'] != 'succeeded':
                raise ContractError('Adapter typed failure')
            task['status'] = 'succeeded'
            batch['status'] = 'queued' if any(t['status'] == 'queued' for t in batch['tasks']) else 'succeeded'
            self.event('task.succeeded', task['id'])
        except Exception as error:
            # No arbitrary exception text/input/credential-bearing argv in durable logs.
            task.update(status='needs_attention', errorType=type(error).__name__,
                        error='Task failed or evidence unavailable; inspect attempt')
            batch['status'] = 'needs_attention'
            self.event('task.needs_attention', task['id'])
        self.save()
        response = {'status': task['status'], 'key': batch['key'], 'taskId': task['id']}
        if batch['status'] == 'needs_attention' and batch['retryMode'] == 'automatic':
            try:
                response['retryQueued'] = self.retry(batch['key'])['id']
            except ContractError:
                pass  # Invalid/unknown/unsafe failure remains held.
        return response

    def retry(self, key):
        batch = self.batch(key)
        if (not self.allow_demo_retry or batch['mode'] != 'demo' or batch['status'] != 'needs_attention'
                or batch['retryUsed'] >= batch['retryLimit']):
            raise ContractError('Retry not authorized or budget exhausted')
        old = next(t for t in reversed(batch['tasks']) if t['status'] == 'needs_attention')
        path = Path(old.get('resultPath', ''))
        if not path.is_file() or path.is_symlink() or filehash(path) != old.get('resultHash'):
            raise ContractError('No durable retry-safe result')
        result = json.loads(path.read_text())
        outcome(result, {'taskId': old['id'], 'requestHash': batch['requestHash'], 'mode': batch['mode']})
        if (result['status'] != 'failed' or result.get('failureKind') != 'communication'
                or result['resources'] != 'released'):
            raise ContractError('Only communication failure with released resources may retry')
        self.snapshot(batch)
        batch['retryUsed'] += 1
        self.event('retry.reserved', old['id'])
        self.save()  # Reserve budget durably before a new attempt can dispatch.
        new = {**old, 'id': 'task-' + str(uuid.uuid4()), 'attempt': old['attempt'] + 1,
               'status': 'queued', 'previousAttempt': old['id']}
        for key in ('resultPath', 'resultHash', 'error', 'errorType'):
            new.pop(key, None)
        batch['tasks'].insert(batch['tasks'].index(old) + 1, new)
        old['status'] = 'failed'
        batch['status'] = 'queued'
        self.event('retry.queued', new['id'])
        self.save()
        return new

    def handle(self, message):
        if not isinstance(message, dict) or message.get('apiVersion') != API:
            raise ContractError('Unsupported API version')
        op = message.get('op')
        if op == 'submit':
            return self.submit(message)
        if op == 'validate':
            return submit({**message, 'op': 'submit'})
        allowed = {'status': {'apiVersion', 'op'}, 'tick': {'apiVersion', 'op'},
                   'pause': {'apiVersion', 'op'}, 'resume': {'apiVersion', 'op'},
                   'retry': {'apiVersion', 'op', 'key'}, 'cancel': {'apiVersion', 'op', 'key'}}
        if op not in allowed or set(message) != allowed[op]:
            raise ContractError('Unknown operation/fields')
        if op == 'status':
            return json.loads(canonical(self.state))
        if op == 'tick':
            return self.tick()
        if op == 'retry':
            return self.retry(message['key'])
        if op == 'cancel':
            batch = self.batch(message['key'])
            if batch['status'] != 'queued':
                raise ContractError('Only queued batch can cancel; never kill active or unknown work')
            for task in batch['tasks']:
                if task['status'] == 'queued':
                    task['status'] = 'cancelled'
            batch['status'] = 'cancelled'
            self.event('batch.cancelled')
            self.save()
            return batch
        self.state['paused'] = op == 'pause'
        self.event('core.' + op)
        self.save()
        return {'paused': self.state['paused']}
