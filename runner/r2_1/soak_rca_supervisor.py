#!/usr/bin/env python3
"""R2.1-C soak RCA supervisor: one GPU child at a time, stage-aware watchdog.

Responsibilities:
  - serialize cases (never two GPU children)
  - separate stdout/stderr files (no PIPE deadlock)
  - sample child liveness/CPU/RSS/threads via psutil every 5 s
  - detect stall by JSONL growth with PER-STAGE grace (compile-heavy stages
    get longer grace than inference-loop stages)
  - forensic snapshot on stall (JSONL tail, process sample), then kill the
    owned child only
  - differentiate: NORMAL_EXIT / NONZERO_EXIT / TIMEOUT_STALL(last_stage) /
    INTERPRETER_SHUTDOWN_HANG / SPAWN_FAIL
  - write per-case receipt + supervisor summary JSON

Usage:
  python soak_rca_supervisor.py --plan fault        # instrument validation
  python soak_rca_supervisor.py --plan matrix       # C2 case matrix
  python soak_rca_supervisor.py --case <name> ...   # single custom case
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone

import psutil

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RUN_ID_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..",
                           "reports", "r2_1", "run_id.txt")
PY = os.path.join(REPO, "venv-winml", "Scripts", "python.exe")
CHILD = os.path.join(REPO, "runner", "r2_1", "soak_rca_child.py")
MODEL = os.path.join(REPO, "artifacts", "models", "yolo26n.onnx")

# per-stage no-progress grace (seconds). compile/release/gc can legitimately
# take minutes (YOLO26n MIGraphX cold compile ~42 s); inference-loop stages
# must log at least every ~30 s (heartbeat) / log_every iters.
STAGE_GRACE = {
    "create_session": 420, "session_release": 420, "gc_collect": 420,
    "recreate": 420, "warmup": 300, "register_ep": 300, "resolve_device": 300,
    "spawn": 300, "run": 300, "fault": 60, "default": 300,
}
SAMPLE_EVERY = 5.0


def utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def stage_of(last_event: dict) -> str:
    st = str(last_event.get("stage", "spawn"))
    base = st.split(":")[0]
    return base if base in STAGE_GRACE else "default"


def parse_jsonl_tail(path, n=40):
    try:
        with open(path, "rb") as f:
            data = f.read()
        lines = data.decode("utf-8", errors="replace").splitlines()
        return [l for l in lines[-n:]], len(lines), data[-200000:]
    except OSError:
        return [], 0, b""


def sample_proc(proc: psutil.Popen) -> dict:
    try:
        with proc.oneshot():
            cpu = proc.cpu_times()
            mem = proc.memory_info()
            return {
                "t": utc_now(), "alive": True,
                "rss_mb": round(mem.rss / 1048576, 1),
                "threads": proc.num_threads(),
                "handles": proc.num_handles(),
                "cpu_user_s": round(cpu.user, 2), "cpu_system_s": round(cpu.system, 2),
                "status": proc.status(),
                "cpu_rate": None,  # filled by caller from delta
            }
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return {"t": utc_now(), "alive": False}


def run_case(case: dict, out_dir: str) -> dict:
    name = case["name"]
    jsonl = os.path.join(out_dir, f"{name}.jsonl")
    stdout_p = os.path.join(out_dir, f"{name}.stdout.log")
    stderr_p = os.path.join(out_dir, f"{name}.stderr.log")
    for p in (jsonl, stdout_p, stderr_p):
        if os.path.exists(p):
            os.remove(p)

    cmd = [PY, "-I", CHILD, "--model", case.get("model", MODEL),
           "--ep", case["ep"], "--minutes", str(case["minutes"]),
           "--recreate-every", str(case.get("recreate_every", 0)),
           "--out", jsonl, "--log-every", str(case.get("log_every", 500)),
           "--fault", case.get("fault", "none"),
           "--teardown", case.get("teardown", "none"),
           "--exit-mode", case.get("exit_mode", "os_exit")]
    if case.get("r2_inputs"):
        cmd.append("--r2-inputs")
    t0 = time.time()
    receipt = {"case": name, "cmd": " ".join(cmd[1:6]) + " ...",
               "argv": cmd[1:], "budget_s": round(case["minutes"] * 60),
               "started_utc": utc_now(), "samples": []}

    fo = open(stdout_p, "wb")
    fe = open(stderr_p, "wb")
    try:
        proc = psutil.Popen(cmd, stdout=fo, stderr=fe, cwd=REPO,
                            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
    except Exception as e:  # noqa: BLE001
        fo.close(); fe.close()
        receipt.update({"classification": "SPAWN_FAIL", "error": str(e),
                        "finished_utc": utc_now()})
        return receipt
    receipt["pid"] = proc.pid

    hard_bound = case["minutes"] * 60 + 900  # budget + max grace + snapshot
    stall_grace_run = case.get("stall_s", 300)
    last_len = 0
    last_progress_t = time.time()
    last_event = {}
    prev_cpu_total = None
    classification = None
    stall_detail = None
    next_sample = 0.0

    while True:
        now = time.time()
        alive = proc.poll() is None
        lines, n_lines, _tail = parse_jsonl_tail(jsonl, 1)
        if n_lines != last_len:
            last_len = n_lines
            last_progress_t = now
            try:
                last_event = json.loads(lines[-1]) if lines else {}
            except json.JSONDecodeError:
                pass
        s = sample_proc(proc)
        if prev_cpu_total is not None and s.get("alive"):
            d = (s["cpu_user_s"] + s["cpu_system_s"]) - prev_cpu_total
            s["cpu_rate"] = round(d / max(now - (next_sample - SAMPLE_EVERY), 1e-3), 3)
        prev_cpu_total = (s.get("cpu_user_s", 0) + s.get("cpu_system_s", 0)) \
            if s.get("alive") else None
        if now >= next_sample:
            receipt["samples"].append(s)
            next_sample = now + SAMPLE_EVERY

        stage = stage_of(last_event)
        grace = stall_grace_run if stage == "run" else STAGE_GRACE.get(stage, 300)

        if not alive:
            rc = proc.returncode
            got_interpreter_exit = str(last_event.get("stage")) == "interpreter_exit"
            if rc == 0 and got_interpreter_exit:
                classification = "NORMAL_EXIT"
            elif rc == 0:
                classification = "EXIT_0_WITHOUT_INTERPRETER_EVENT"
            else:
                classification = f"NONZERO_EXIT_rc{rc}"
            break

        stalled = (now - last_progress_t) > grace
        interpreter_hung = (str(last_event.get("stage")) == "interpreter_exit"
                            and (now - last_progress_t) > 180)
        if interpreter_hung:
            classification = "INTERPRETER_SHUTDOWN_HANG"
            stall_detail = {"stage": "interpreter_exit",
                            "no_progress_s": round(now - last_progress_t, 1)}
            break
        if stalled:
            classification = "TIMEOUT_STALL"
            stall_detail = {"stage": last_event.get("stage"), "stage_base": stage,
                            "iter": last_event.get("iter"),
                            "no_progress_s": round(now - last_progress_t, 1),
                            "grace_s": grace}
            break
        if now - t0 > hard_bound:
            classification = "HARD_BOUND_EXCEEDED"
            stall_detail = {"stage": last_event.get("stage")}
            break
        time.sleep(1.0)

    # forensic snapshot BEFORE killing
    if stall_detail is not None or classification in ("HARD_BOUND_EXCEEDED",
                                                      "INTERPRETER_SHUTDOWN_HANG"):
        tail_lines, _, _ = parse_jsonl_tail(jsonl, 30)
        receipt["forensics"] = {
            "last_events": tail_lines,
            "final_sample": sample_proc(proc),
            "stall_detail": stall_detail,
        }
        # kill owned child tree only
        try:
            for ch in proc.children(recursive=True):
                ch.kill()
            proc.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
        try:
            proc.wait(timeout=15)
        except psutil.TimeoutExpired:
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                           capture_output=True)
    try:
        proc.wait(timeout=5)
    except Exception:  # noqa: BLE001
        pass
    fo.close(); fe.close()

    lines, n_lines, _ = parse_jsonl_tail(jsonl, 1)
    last_event_parsed = None
    if lines:
        try:
            last_event_parsed = json.loads(lines[-1])
        except json.JSONDecodeError:
            last_event_parsed = {"stage": "<unparseable>", "raw": lines[-1][:200]}
    receipt.update({
        "classification": classification,
        "exit_code": proc.returncode,
        "wall_s": round(time.time() - t0, 1),
        "jsonl_events": n_lines,
        "last_event": last_event_parsed,
        "finished_utc": utc_now(),
    })
    rc_path = os.path.join(out_dir, f"{name}.receipt.json")
    with open(rc_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(receipt, f, indent=1, default=str)
    print(f"[{name}] {classification} wall={receipt['wall_s']}s "
          f"events={n_lines} last={last_event_parsed and last_event_parsed.get('stage')}",
          flush=True)
    return receipt


FAULT_PLAN = [
    # validate the watchdog itself: each fault must produce the right verdict
    {"name": "fault_normal_exit", "ep": "MIGraphXExecutionProvider", "minutes": 1,
     "fault": "normal_exit", "stall_s": 60},
    {"name": "fault_nonzero_exit", "ep": "MIGraphXExecutionProvider", "minutes": 1,
     "fault": "nonzero_exit", "stall_s": 60},
    {"name": "fault_sleep_prerun", "ep": "MIGraphXExecutionProvider", "minutes": 0.2,
     "fault": "sleep_prerun", "stall_s": 25},
    {"name": "fault_blocked_recreate", "ep": "MIGraphXExecutionProvider", "minutes": 3,
     "recreate_every": 50, "log_every": 25, "fault": "blocked_recreate", "stall_s": 60},
]

MATRIX_PLAN = [
    # C2.1 AMDGPU control: persistent session, no recreate
    {"name": "c1_amd_control_persistent", "ep": "AMDGPUExecutionProvider",
     "minutes": 6, "recreate_every": 0},
    # C2.2 MIGraphX persistent session, NO recreation
    {"name": "c2_mgx_persistent", "ep": "MIGraphXExecutionProvider",
     "minutes": 12, "recreate_every": 0},
    # C2.3 MIGraphX recreate every 2000 (R2 setting) - prime suspect
    {"name": "c3_mgx_recreate2000", "ep": "MIGraphXExecutionProvider",
     "minutes": 16, "recreate_every": 2000},
    # C2.4 MIGraphX recreate every 4000
    {"name": "c4_mgx_recreate4000", "ep": "MIGraphXExecutionProvider",
     "minutes": 10, "recreate_every": 4000},
    # C2.5 MIGraphX single session then EXPLICIT teardown + interpreter exit
    {"name": "c5_mgx_teardown_interpreter", "ep": "MIGraphXExecutionProvider",
     "minutes": 5, "recreate_every": 0, "teardown": "explicit",
     "exit_mode": "interpreter"},
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", choices=["fault", "matrix", "all"], default="fault")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--case", help="run a single named case from the plans")
    args = ap.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)
    # guard: no orphaned prior experiment children
    me = os.getpid()
    orphans = []
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            if p.info["pid"] == me or p.info["name"] != "python.exe":
                continue
            cl = " ".join(p.info["cmdline"] or [])
            if "soak_rca_child.py" in cl:
                orphans.append(p.info["pid"])
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    if orphans:
        print(f"REFUSING_TO_START: orphaned soak children exist: {orphans}")
        return 2

    plans = []
    if args.plan in ("fault", "all"):
        plans += FAULT_PLAN
    if args.plan in ("matrix", "all"):
        plans += MATRIX_PLAN
    if args.case:
        plans = [c for c in FAULT_PLAN + MATRIX_PLAN if c["name"] == args.case]
        if not plans:
            print(f"unknown case {args.case}")
            return 2

    summary = {"started_utc": utc_now(), "host_note": "single GPU child at a time",
               "cases": []}
    for case in plans:
        try:
            summary["cases"].append(run_case(case, args.out_dir))
        except Exception:  # noqa: BLE001
            summary["cases"].append({
                "case": case["name"], "classification": "SUPERVISOR_ERROR",
                "error": traceback.format_exc(limit=6)})
    summary["finished_utc"] = utc_now()
    with open(os.path.join(args.out_dir, "supervisor_summary.json"), "w",
              encoding="utf-8", newline="\n") as f:
        json.dump(summary, f, indent=1, default=str)
    ok = all(c.get("classification") not in (None, "SUPERVISOR_ERROR") for c in summary["cases"])
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
