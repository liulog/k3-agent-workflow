#!/usr/bin/env python3
"""Fixed JSON entrypoint; no experiment-specific executors or model shell commands."""
import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from adapters.experiment import load_spec
from adapters.offline_agent import OfflineAgent
from core_v1 import API, ContractError, Core


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("op", choices=["validate", "run", "status", "pause", "resume", "cancel"])
    parser.add_argument("--spec")
    parser.add_argument("--root", help="Dedicated state directory; shared by all calls to this worker")
    parser.add_argument("--key", help="Batch to cancel")
    args = parser.parse_args()
    os.umask(0o077)
    try:
        if args.op in ("validate", "run") and not args.spec:
            raise ContractError("--spec is required")
        if args.op != "validate" and not args.root:
            raise ContractError("--root is required")
        request = load_spec(args.spec) if args.op in ("validate", "run") else None
        if args.op == "validate":
            result = {"valid": True, "jobs": len(request["jobs"]),
                      "commandsExecuted": False, "boardAccessed": False}
        else:
            with Core(args.root, {"offline-agent-v1": OfflineAgent()}) as core:
                if args.op == "run":
                    batch = core.submit(request)
                    # A run advances only its own batch and never drains someone else's work.
                    while batch["status"] == "queued" and not core.state["paused"]:
                        first = next((b for b in core.state["batches"]
                                      if b["status"] not in ("succeeded", "cancelled")), None)
                        if first is not batch:
                            break
                        core.tick()
                    result = {"key": batch["key"], "status": batch["status"],
                              "simulated": True, "boardAccessed": False,
                              "execution": "NOT_EXECUTED", "tasks": batch["tasks"]}
                else:
                    message = {"apiVersion": API, "op": args.op}
                    if args.op == "cancel":
                        if not args.key:
                            raise ContractError("--key is required")
                        message["key"] = args.key
                    result = core.handle(message)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if args.op != "run" or result["status"] == "succeeded" else 2
    except (OSError, ValueError, TypeError, KeyError) as error:
        # Do not print supplied params, prompts, credentials or arbitrary exception text.
        print(json.dumps({"ok": False, "errorType": type(error).__name__,
                          "message": "Invalid input, unavailable resource, or execution guard rejected operation"}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
