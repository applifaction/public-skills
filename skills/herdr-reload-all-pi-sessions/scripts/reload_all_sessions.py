#!/usr/bin/env python3
"""Safely submit /reload to every live Pi session managed by Herdr."""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

DEFAULT_CONCURRENCY = 6
DEFAULT_WAIT_TIMEOUT = 30 * 60
POLL_SECONDS = 1.5
STATE_ROOT = Path.home() / ".local" / "state" / "herdr-reload-all-pi-sessions"


def herdr(args: list[str], timeout: float = 15) -> dict:
    process = subprocess.run(
        ["herdr", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    raw = process.stdout.strip() or process.stderr.strip()
    try:
        payload = json.loads(raw) if raw else {}
    except json.JSONDecodeError as error:
        raise RuntimeError(f"herdr returned non-JSON output: {raw[:500]}") from error
    if process.returncode != 0 or payload.get("error"):
        detail = payload.get("error") or raw or f"exit {process.returncode}"
        raise RuntimeError(json.dumps(detail, ensure_ascii=False))
    result = payload.get("result", payload)
    return result if isinstance(result, dict) else {"value": result}


def list_pi_agents() -> list[dict]:
    agents = herdr(["agent", "list"]).get("agents", [])
    deduplicated: dict[str, dict] = {}
    for agent in agents:
        pane_id = agent.get("pane_id")
        if agent.get("agent") == "pi" and pane_id:
            deduplicated[pane_id] = agent
    return sorted(deduplicated.values(), key=lambda item: item["pane_id"])


def get_agent(pane_id: str) -> dict:
    result = herdr(["agent", "get", pane_id])
    agent = result.get("agent", result)
    return agent if isinstance(agent, dict) else {}


def prompt_reload(agent: dict) -> dict:
    pane_id = agent["pane_id"]
    started = time.perf_counter()
    try:
        response = herdr(["agent", "prompt", pane_id, "/reload"], timeout=15)
        return {
            "paneId": pane_id,
            "statusBefore": agent.get("agent_status"),
            "outcome": "submitted",
            "durationMs": round((time.perf_counter() - started) * 1000, 2),
            "response": response,
        }
    except Exception as error:  # noqa: BLE001 - every pane must produce a result
        return {
            "paneId": pane_id,
            "statusBefore": agent.get("agent_status"),
            "outcome": "failed",
            "durationMs": round((time.perf_counter() - started) * 1000, 2),
            "error": str(error),
        }


def pane_file_name(pane_id: str) -> str:
    return pane_id.replace(":", "-").replace("/", "-")


def write_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def deferred_reload(pane_id: str, run_dir: Path, wait_timeout: int) -> int:
    deadline = time.monotonic() + wait_timeout
    output_path = run_dir / f"{pane_file_name(pane_id)}.result.json"
    while time.monotonic() < deadline:
        try:
            agent = get_agent(pane_id)
        except Exception as error:  # transient Herdr gaps are retried
            time.sleep(POLL_SECONDS)
            last_error = str(error)
            continue
        if agent.get("agent") != "pi":
            write_json(output_path, {
                "paneId": pane_id,
                "outcome": "vanished",
                "message": "Target is no longer a Pi session.",
            })
            return 2
        status = agent.get("agent_status")
        if status in {"idle", "done"}:
            result = prompt_reload(agent)
            result["deferred"] = True
            write_json(output_path, result)
            print(json.dumps(result, ensure_ascii=False), flush=True)
            return 0 if result["outcome"] == "submitted" else 2
        time.sleep(POLL_SECONDS)
    result = {
        "paneId": pane_id,
        "outcome": "timeout",
        "waitTimeoutSeconds": wait_timeout,
        "lastError": locals().get("last_error"),
    }
    write_json(output_path, result)
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 3


def schedule_deferred(agent: dict, run_dir: Path, wait_timeout: int) -> dict:
    pane_id = agent["pane_id"]
    log_path = run_dir / f"{pane_file_name(pane_id)}.worker.log"
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--deferred-pane",
        pane_id,
        "--run-dir",
        str(run_dir),
        "--wait-timeout",
        str(wait_timeout),
    ]
    with log_path.open("ab") as log:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
    return {
        "paneId": pane_id,
        "statusBefore": agent.get("agent_status"),
        "outcome": "scheduled",
        "workerPid": process.pid,
        "log": str(log_path),
    }


def plan(agents: list[dict]) -> dict:
    immediate = [agent for agent in agents if agent.get("agent_status") in {"idle", "done"}]
    deferred = [agent for agent in agents if agent.get("agent_status") in {"working", "blocked"}]
    skipped = [agent for agent in agents if agent not in immediate and agent not in deferred]
    return {
        "total": len(agents),
        "immediate": immediate,
        "deferred": deferred,
        "skipped": skipped,
    }


def compact_agent(agent: dict) -> dict:
    return {
        "paneId": agent.get("pane_id"),
        "workspaceId": agent.get("workspace_id"),
        "status": agent.get("agent_status"),
        "title": (agent.get("terminal_title_stripped") or agent.get("name") or "")[:160],
    }


def execute_all(concurrency: int, wait_timeout: int) -> dict:
    agents = list_pi_agents()
    reload_plan = plan(agents)
    run_id = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    run_dir = STATE_ROOT / run_id
    run_dir.mkdir(parents=True, exist_ok=False)

    immediate_results: list[dict] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [pool.submit(prompt_reload, agent) for agent in reload_plan["immediate"]]
        for future in concurrent.futures.as_completed(futures):
            immediate_results.append(future.result())
    immediate_results.sort(key=lambda item: item["paneId"])

    deferred_results = [
        schedule_deferred(agent, run_dir, wait_timeout)
        for agent in reload_plan["deferred"]
    ]
    skipped_results = [
        {**compact_agent(agent), "outcome": "skipped"}
        for agent in reload_plan["skipped"]
    ]
    summary = {
        "version": 1,
        "runId": run_id,
        "createdAt": dt.datetime.now(dt.timezone.utc).isoformat(),
        "runDir": str(run_dir),
        "counts": {
            "total": len(agents),
            "submitted": sum(item["outcome"] == "submitted" for item in immediate_results),
            "failed": sum(item["outcome"] == "failed" for item in immediate_results),
            "scheduled": len(deferred_results),
            "skipped": len(skipped_results),
        },
        "immediate": immediate_results,
        "deferred": deferred_results,
        "skipped": skipped_results,
    }
    write_json(run_dir / "summary.json", summary)
    return summary


def dry_run() -> dict:
    agents = list_pi_agents()
    reload_plan = plan(agents)
    return {
        "mode": "dry-run",
        "counts": {
            "total": len(agents),
            "immediate": len(reload_plan["immediate"]),
            "deferred": len(reload_plan["deferred"]),
            "skipped": len(reload_plan["skipped"]),
        },
        "deferred": [compact_agent(agent) for agent in reload_plan["deferred"]],
        "skipped": [compact_agent(agent) for agent in reload_plan["skipped"]],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="Submit or schedule /reload for every Pi session.")
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    parser.add_argument("--wait-timeout", type=int, default=DEFAULT_WAIT_TIMEOUT)
    parser.add_argument("--deferred-pane", help=argparse.SUPPRESS)
    parser.add_argument("--run-dir", type=Path, help=argparse.SUPPRESS)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.deferred_pane:
        if not args.run_dir:
            raise SystemExit("--run-dir is required with --deferred-pane")
        return deferred_reload(args.deferred_pane, args.run_dir, args.wait_timeout)
    if not 1 <= args.concurrency <= 16:
        raise SystemExit("--concurrency must be between 1 and 16")
    if args.wait_timeout < 30:
        raise SystemExit("--wait-timeout must be at least 30 seconds")
    result = execute_all(args.concurrency, args.wait_timeout) if args.execute else dry_run()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.execute and result["counts"]["failed"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
