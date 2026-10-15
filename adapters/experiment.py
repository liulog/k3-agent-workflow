"""Experiment input boundary. The scheduler never interprets prompts or skills."""
import hashlib
import json
from pathlib import Path

from core_v1.contract import API, ContractError, fields, name, submit

SPEC_VERSION = "k3.experiment/v1"
LIMIT = 256 * 1024


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ContractError("Duplicate JSON key")
        result[key] = value
    return result


def load_spec(path):
    path = Path(path).resolve(strict=True)
    with path.open("rb") as stream:
        raw = stream.read(LIMIT + 1)
    if len(raw) > LIMIT:
        raise ContractError("Experiment exceeds 256 KiB")
    return prepare(json.loads(raw, object_pairs_hook=unique_object), path.parent)


def prompt(value):
    if not isinstance(value, str) or not value.strip() or len(value.encode()) > 8000:
        raise ContractError("Prompt must contain 1..8000 UTF-8 bytes")
    return value


def freeze_skills(paths, base):
    if not isinstance(paths, list) or len(paths) > 8:
        raise ContractError("Expected at most eight skill paths")
    result = []
    seen = set()
    for entry in paths:
        if not isinstance(entry, str) or not entry:
            raise ContractError("Expected explicit SKILL.md path")
        path = Path(entry)
        path = path if path.is_absolute() else Path(base) / path
        if path.name != "SKILL.md" or any(p.is_symlink() for p in [path, *path.parents]):
            raise ContractError("Expected ordinary, non-symlinked SKILL.md")
        path = path.resolve(strict=True)
        if path in seen or not path.is_file():
            raise ContractError("Duplicate or non-file skill")
        seen.add(path)
        with path.open("rb") as stream:
            raw = stream.read(65537)
        if len(raw) > 65536:
            raise ContractError("Skill exceeds 64 KiB")
        text = raw.decode("utf-8")
        if not text.strip():
            raise ContractError("Empty skill")
        result.append({"path": str(path), "baseDir": str(path.parent),
                       "sha256": hashlib.sha256(raw).hexdigest(), "content": text})
    return result


def prepare(spec, base):
    fields(spec, {"apiVersion", "key", "mode", "prompt", "skills", "tests"},
           {"apiVersion", "key", "mode", "prompt", "tests"})
    if spec["apiVersion"] != SPEC_VERSION:
        raise ContractError("Unsupported experiment version")
    if spec["mode"] not in ("plan", "demo"):
        raise ContractError("Real adapter is not ready; no model/build/board execution")
    common = prompt(spec["prompt"])
    tests = spec["tests"]
    if not isinstance(tests, list) or not 1 <= len(tests) <= 128:
        raise ContractError("Expected 1..128 ordered tests")
    common_skills = spec.get("skills", [])
    if not isinstance(common_skills, list):
        raise ContractError("Expected skill list")
    jobs = []
    for test in tests:
        fields(test, {"id", "prompt", "skills", "params"}, {"id", "prompt", "params"})
        name(test["id"])
        if not isinstance(test["params"], dict) or not isinstance(test.get("skills", []), list):
            raise ContractError("Expected params object and skill list")
        jobs.append({"key": test["id"], "kind": "task", "adapter": "offline-agent-v1",
                     "params": {"instructions": {"experiment": common, "task": prompt(test["prompt"])},
                                "inputs": test["params"],
                                "skills": freeze_skills(common_skills + test.get("skills", []), base)}})
    return submit({"apiVersion": API, "op": "submit", "key": spec["key"],
                   "mode": spec["mode"], "jobs": jobs})
