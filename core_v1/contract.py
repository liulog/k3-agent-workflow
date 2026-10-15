"""Small task/result contract. Task semantics belong in the adapter, not core."""
import hashlib
import json
import re

API = 'k3.workflow/v1'
MAX_BYTES = 256 * 1024


class ContractError(ValueError):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False, allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def name(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,95}', value):
        raise ContractError('Invalid name/key')
    return value


def data_only(value, depth=0):
    if depth > 16:
        raise ContractError('Input nesting too deep')
    if isinstance(value, dict):
        for key, item in value.items():
            if (not isinstance(key, str)
                    or re.search(r'password|passwd|secret|token|api.?key|private.?key', key, re.I)
                    or key in ('__proto__', 'constructor', 'prototype')):
                raise ContractError('Private/unsafe data key forbidden; use operator-side credential references')
            data_only(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            data_only(item, depth + 1)
    elif value is not None and not isinstance(value, (str, bool, int, float)):
        raise ContractError('Only JSON data allowed')


def fields(value, allowed, required):
    if not isinstance(value, dict) or set(value) - set(allowed) or set(required) - set(value):
        raise ContractError('Missing/unknown contract fields')


def submit(value):
    fields(value, {'apiVersion', 'op', 'key', 'mode', 'jobs', 'retryLimit', 'retryMode'},
           {'apiVersion', 'op', 'key', 'mode', 'jobs'})
    if value['apiVersion'] != API or value['op'] != 'submit':
        raise ContractError('Unsupported API version/operation')
    name(value['key'])
    if value['mode'] not in ('plan', 'demo'):
        raise ContractError('Real execution is deliberately disabled in the isolated v1 pilot')
    jobs = value['jobs']
    if not isinstance(jobs, list) or not 1 <= len(jobs) <= 128:
        raise ContractError('Expected 1..128 ordered jobs')
    keys, normalized = set(), []
    for job in jobs:
        fields(job, {'key', 'kind', 'adapter', 'params'}, {'key', 'kind', 'adapter', 'params'})
        key, adapter = name(job['key']), name(job['adapter'])
        name(job['kind'])
        if key in keys or not isinstance(job['params'], dict):
            raise ContractError('Duplicate job key/invalid params')
        keys.add(key)
        data_only(job['params'])
        normalized.append({'key': key, 'kind': job['kind'], 'adapter': adapter, 'params': job['params']})
    retry = value.get('retryLimit', 0)
    if type(retry) is not int or retry not in (0, 1):
        raise ContractError('Retry limit must be 0 or 1')
    retry_mode = value.get('retryMode', 'manual')
    if retry_mode not in ('manual', 'automatic'):
        raise ContractError('Retry mode must be manual/automatic')
    result = {'apiVersion': API, 'op': 'submit', 'key': value['key'], 'mode': value['mode'],
              'jobs': normalized, 'retryLimit': retry, 'retryMode': retry_mode}
    if len(canonical(result)) > MAX_BYTES:
        raise ContractError('Request exceeds 256 KiB')
    # Isolate the stored request from callers mutating their original dicts.
    return json.loads(canonical(result))


def outcome(value, context):
    required = {'apiVersion', 'taskId', 'requestHash', 'mode', 'simulated', 'commandsExecuted',
                'boardAccessed', 'status', 'resources', 'checks', 'data'}
    fields(value, required | {'failureKind'}, required)
    if value['apiVersion'] != API or any(value[k] != context[k] for k in ('taskId', 'requestHash', 'mode')):
        raise ContractError('Adapter result identity mismatch')
    if (type(value['commandsExecuted']) is not bool or value['boardAccessed'] is not False
            or value['simulated'] is not True
            or (context['mode'] == 'plan' and value['commandsExecuted'] is not False)):
        raise ContractError('Pilot output must be explicitly offline/simulated; plan never executes commands')
    if (value['status'] not in ('succeeded', 'failed') or value['resources'] not in ('released', 'unknown')
            or not isinstance(value['checks'], dict) or not value['checks']
            or any(type(v) is not bool for v in value['checks'].values())):
        raise ContractError('Incomplete adapter result')
    if value['status'] == 'succeeded' and (value['resources'] != 'released'
            or not all(value['checks'].values()) or value.get('failureKind')):
        raise ContractError('Adapter completion is not gate success')
    if value['status'] == 'failed' and value.get('failureKind') not in (
            'communication', 'identity', 'evidence', 'authentication', 'frequency', 'kernel', 'resource', 'unknown'):
        raise ContractError('Failed result requires typed failure kind')
    data_only(value['data'])
    if len(canonical(value)) > 1024 * 1024:
        raise ContractError('Adapter result too large')
    return value


def receipt(context, status='succeeded', failure=None, data=None):
    return {'apiVersion': API, 'taskId': context['taskId'], 'requestHash': context['requestHash'],
            'mode': context['mode'], 'simulated': True,
            'commandsExecuted': context.get('commandsExecuted', False), 'boardAccessed': False,
            'status': status, 'resources': 'released',
            'checks': {'offline': True, 'completed': status == 'succeeded'}, 'data': data or {},
            **({'failureKind': failure} if failure else {})}
