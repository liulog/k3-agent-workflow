"""Fixed offline worker. No subprocess, model API, build or hardware access.

This exercises the same frozen task envelope a reviewed real adapter must consume.
It deliberately does not pretend to interpret prompts or execute a benchmark.
"""
import hashlib
from pathlib import Path

from core_v1.contract import ContractError, receipt


class OfflineAgent:
    @property
    def fingerprint(self):
        return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()

    def run(self, context):
        task = context["params"]
        for skill in task["skills"]:
            actual = hashlib.sha256(skill["content"].encode()).hexdigest()
            if actual != skill["sha256"]:
                raise ContractError("Frozen skill content does not match its hash")
        return receipt(context, data={
            "simulated": True,
            "execution": "NOT_EXECUTED",
            "modelAPIsCalled": False,
            "actualMeasurements": None,
            "received": task,
        })
