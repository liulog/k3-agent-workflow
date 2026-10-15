"""Offline regression only: temporary files, stdlib mocks and fixed Python CLI."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from adapters.experiment import prepare, load_spec
from adapters.offline_agent import OfflineAgent
from core_v1 import API, Core, ContractError
from core_v1.contract import receipt

REPO = Path(__file__).resolve().parents[1]


class Worker:
    fingerprint = "fixture-v1"

    def __init__(self, run):
        self.run = run


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.root = self.base / "state"
        self.spec = json.loads((REPO / "examples/workflow/offline.json").read_text())

    def request(self):
        return prepare(self.spec, self.base)

    def core(self, worker=None):
        return Core(self.root, {"offline-agent-v1": worker or OfflineAgent()})

    def test_json_changes_do_not_require_scripts(self):
        self.spec["tests"][0]["params"] = {"copies": 4, "rounds": 9, "benchmark": "new-existing-tool-case"}
        with self.core() as core:
            batch = core.submit(self.request())
            core.tick()
            result = json.loads(Path(batch["tasks"][0]["resultPath"]).read_text())
            self.assertEqual(result["data"]["received"]["inputs"], self.spec["tests"][0]["params"])
            self.assertTrue(result["simulated"])
            self.assertIsNone(result["data"]["actualMeasurements"])

    def test_frozen_skill_survives_source_edit(self):
        skill = self.base / "SKILL.md"
        skill.write_text("Original skill: do not run hardware")
        self.spec["skills"] = ["SKILL.md"]
        request = self.request()
        skill.write_text("Changed after submission")
        with self.core() as core:
            batch = core.submit(request)
            core.tick()
            result = json.loads(Path(batch["tasks"][0]["resultPath"]).read_text())
            self.assertEqual(result["data"]["received"]["skills"][0]["content"], "Original skill: do not run hardware")

    def test_skill_change_same_key_conflicts(self):
        skill = self.base / "SKILL.md"
        skill.write_text("First")
        self.spec["skills"] = ["SKILL.md"]
        with self.core() as core:
            core.submit(self.request())
            skill.write_text("Second")
            with self.assertRaises(ContractError):
                core.submit(self.request())

    def test_skill_symlink_rejected(self):
        target = self.base / "document"
        target.write_text("reference")
        (self.base / "SKILL.md").symlink_to(target)
        self.spec["skills"] = ["SKILL.md"]
        with self.assertRaises(ContractError):
            self.request()

    def test_real_rejected(self):
        self.spec["mode"] = "real"
        with self.assertRaises(ContractError):
            self.request()

    def test_executable_override_rejected(self):
        self.spec["command"] = "untrusted-command"
        with self.assertRaises(ContractError):
            self.request()

    def test_duplicate_json_key(self):
        path = self.base / "bad.json"
        path.write_text('{"mode":"plan","mode":"real"}')
        with self.assertRaises(ContractError):
            load_spec(path)

    def test_duplicate_task(self):
        self.spec["tests"].append(copy.deepcopy(self.spec["tests"][0]))
        with self.assertRaises(ContractError):
            self.request()

    def test_idempotency(self):
        with self.core() as core:
            first = core.submit(self.request())
            self.assertIs(first, core.submit(self.request()))
            self.spec["tests"][0]["prompt"] = "Different task"
            with self.assertRaises(ContractError):
                core.submit(self.request())

    def test_single_owner(self):
        with self.core():
            with self.assertRaises(BlockingIOError):
                self.core()

    def test_fifo(self):
        with self.core() as core:
            first = core.submit(self.request())
            self.spec["key"] = "second"
            second = core.submit(self.request())
            for _ in range(2):
                self.assertEqual(core.tick()["key"], first["key"])
            self.assertEqual(core.tick()["key"], second["key"])

    def test_plan_never_calls_worker(self):
        self.spec["mode"] = "plan"
        with self.core(Worker(lambda _: self.fail("Worker called in plan"))) as core:
            core.submit(self.request())
            self.assertEqual(core.tick()["status"], "succeeded")

    def test_unknown_resources_hold_queue(self):
        def run(ctx):
            result = receipt(ctx)
            result["resources"] = "unknown"
            return result
        with self.core(Worker(run)) as core:
            batch = core.submit(self.request())
            self.assertEqual(core.tick()["status"], "needs_attention")
            self.assertEqual(core.tick()["status"], "needs_attention")
            self.assertEqual(batch["tasks"][1]["status"], "queued")

    def test_wrong_receipt_identity(self):
        def run(ctx):
            result = receipt(ctx)
            result["taskId"] = "wrong"
            return result
        with self.core(Worker(run)) as core:
            core.submit(self.request())
            self.assertEqual(core.tick()["status"], "needs_attention")

    def test_restart_does_not_reexecute(self):
        with self.core() as core:
            batch = core.submit(self.request())
            batch["tasks"][0]["status"] = "running"
            batch["status"] = "running"
            core.save()
        with self.core(Worker(lambda _: self.fail("Replayed interrupted task"))) as core:
            self.assertEqual(core.tick()["status"], "needs_attention")

    def test_existing_attempt_directory_holds_instead_of_crashing(self):
        with self.core() as core:
            batch = core.submit(self.request())
            (self.root / "tasks" / batch["tasks"][0]["id"]).mkdir(parents=True)
            self.assertEqual(core.tick()["status"], "needs_attention")

    def test_snapshot_tamper(self):
        with self.core() as core:
            batch = core.submit(self.request())
            (self.root / "batches" / batch["key"] / "request.json").write_text("{}")
            self.assertEqual(core.tick()["status"], "needs_attention")

    def test_pause_resume_cancel(self):
        with self.core() as core:
            batch = core.submit(self.request())
            core.handle({"apiVersion": API, "op": "pause"})
            self.assertEqual(core.tick()["status"], "paused")
            core.handle({"apiVersion": API, "op": "resume"})
            core.handle({"apiVersion": API, "op": "cancel", "key": batch["key"]})
            self.assertEqual(core.tick()["status"], "idle")

    def test_caller_cannot_mutate_submitted_input(self):
        request = self.request()
        with self.core() as core:
            batch = core.submit(request)
            request["jobs"][0]["params"]["inputs"]["copies"] = 999
            self.assertEqual(batch["tasks"][0]["job"]["params"]["inputs"]["copies"], 8)
            self.assertEqual(core.tick()["status"], "succeeded")

    def test_retry_requires_operator_flag(self):
        request = self.request()
        request["retryLimit"] = 1
        with self.core() as core:
            with self.assertRaises(ContractError):
                core.submit(request)

    def test_retry_once_and_budget_survives_restart(self):
        request = self.request()
        request.update(retryLimit=1, retryMode="automatic")
        worker = Worker(lambda ctx: receipt(ctx, "failed", "communication"))
        with Core(self.root, {"offline-agent-v1": worker}, allow_demo_retry=True) as core:
            batch = core.submit(request)
            self.assertIn("retryQueued", core.tick())
            self.assertEqual(batch["retryUsed"], 1)
            self.assertNotEqual(batch["tasks"][0]["id"], batch["tasks"][1]["id"])
            self.assertTrue(Path(batch["tasks"][0]["resultPath"]).is_file())
        with Core(self.root, {"offline-agent-v1": worker}, allow_demo_retry=True) as core:
            self.assertNotIn("retryQueued", core.tick())
            self.assertEqual(core.batch(request["key"])["retryUsed"], 1)
            self.assertEqual(core.tick()["status"], "needs_attention")

    def test_unsafe_failure_never_retries(self):
        for failure in ("kernel", "authentication", "frequency", "identity", "evidence", "resource", "unknown"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as root:
                worker = Worker(lambda ctx: receipt(ctx, "failed", failure))
                request = self.request()
                request.update(retryLimit=1, retryMode="automatic")
                with Core(root, {"offline-agent-v1": worker}, allow_demo_retry=True) as core:
                    batch = core.submit(request)
                    self.assertNotIn("retryQueued", core.tick())
                    self.assertEqual(batch["retryUsed"], 0)

    def test_adapter_change_stops_dispatch(self):
        worker = Worker(lambda ctx: self.fail("Changed worker was invoked"))
        with self.core(worker) as core:
            core.submit(self.request())
            worker.fingerprint = "changed"
            self.assertEqual(core.tick()["status"], "needs_attention")

    def test_failed_task_cannot_be_cancelled_to_unblock(self):
        with self.core(Worker(lambda ctx: receipt(ctx, "failed", "resource"))) as core:
            batch = core.submit(self.request())
            core.tick()
            with self.assertRaises(ContractError):
                core.handle({"apiVersion": API, "op": "cancel", "key": batch["key"]})

    def test_cli_roundtrip_and_rerun(self):
        command = [sys.executable, str(REPO / "scripts/workflow.py"), "run", "--spec",
                   str(REPO / "examples/workflow/offline.json"), "--root", str(self.root)]
        first = subprocess.run(command, capture_output=True, text=True, check=True)
        second = subprocess.run(command, capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(first.stdout), json.loads(second.stdout))
        self.assertEqual(json.loads(first.stdout)["status"], "succeeded")

    def test_cli_does_not_drain_unrelated_batch(self):
        with self.core() as core:
            self.spec["key"] = "earlier"
            core.submit(self.request())
        command = [sys.executable, str(REPO / "scripts/workflow.py"), "run", "--spec",
                   str(REPO / "examples/workflow/offline.json"), "--root", str(self.root)]
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        with self.core() as core:
            self.assertTrue(all(t["status"] == "queued" for b in core.state["batches"] for t in b["tasks"]))


if __name__ == "__main__":
    unittest.main()
