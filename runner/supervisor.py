"""Persistent overnight supervisor for WIN-MIGRAPHX-GFX1100.

- Survives the launching session (started detached via Win32_Process.Create).
- Heartbeat every 30s -> reports/supervisor_heartbeat.json
- Idempotent resume: task table persisted in ledger["supervisor_tasks"];
  terminal tasks are never re-run.
- Per-task subprocess with timeout; native-crash circuit breaker (3 consecutive
  crashes of the same task -> DANGEROUS_STOP).
- Checkpoint commits to git every ~30 min (+ push attempt).
- Stops creating heavy tasks 25 min before deadline; always runs the
  lightweight wrap-up task (reports/manifest + final status) at the end.
- Holds the system awake via SetThreadExecutionState (process-scoped,
  released on exit; no power-plan changes).
"""
from __future__ import annotations
import argparse, ctypes, datetime, json, os, subprocess, sys, time, traceback

sys.path.insert(0, os.path.dirname(__file__))
import common as C
import ledger as L

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001

CRASH_CODES = {0xC0000005: "ACCESS_VIOLATION", 0xC0000409: "STACK_BUFFER_OVERRUN",
               0x8007000E: "OUT_OF_MEMORY", 0xC00000FD: "STACK_OVERFLOW",
               0xC0000142: "DLL_INIT_FAILED"}


class StayAwake:
    def __enter__(self):
        try:
            ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
        except Exception:
            pass
        return self

    def __exit__(self, *a):
        try:
            ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
        except Exception:
            pass


def hb(repo: str, state: dict):
    state = dict(state)
    state.update({"ts_utc": C.now_pair()["utc"], "ts_local": C.now_pair()["local"],
                  "pid": os.getpid()})
    C.atomic_write_json(os.path.join(repo, "reports", "supervisor_heartbeat.json"), state)


def sh(cmd: list[str], timeout_s: int, cwd: str, log_path: str) -> dict:
    """Run subprocess with hard timeout; capture exit code + output tail."""
    t0 = time.time()
    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    # local proxy for github/PyPI egress (Clash mixed port on this host)
    env.setdefault("HTTPS_PROXY", "http://127.0.0.1:7897")
    env.setdefault("HTTP_PROXY", "http://127.0.0.1:7897")
    with open(log_path, "ab") as lf:
        lf.write(f"\n===== {C.now_pair()['utc']} RUN {cmd} =====\n".encode())
        try:
            p = subprocess.run(cmd, cwd=cwd, timeout=timeout_s, stdout=lf,
                               stderr=subprocess.STDOUT, env=env)
            rc = p.returncode
        except subprocess.TimeoutExpired:
            rc = -999
        except Exception as e:
            lf.write(f"SPAWN_EXC {e!r}\n".encode())
            rc = -998
    return {"exit_code": rc, "seconds": round(time.time() - t0, 1), "log": log_path}


def git_checkpoint(repo: str, push: bool) -> dict:
    out = {"commit": None, "push": None}
    cmds = [["git", "add", "-A", "."], ["git", "commit", "-m",
            f"checkpoint {C.now_pair()['utc']} (supervisor)"]]
    for c in cmds:
        r = subprocess.run(c, cwd=repo, capture_output=True, text=True, timeout=120)
        if c[0] == "commit" and r.returncode == 0:
            out["commit"] = r.stdout.strip().splitlines()[0] if r.stdout.strip() else "ok"
    if push:
        r = subprocess.run(["git", "push", "origin", "main"], cwd=repo,
                           capture_output=True, text=True, timeout=300)
        out["push"] = {"rc": r.returncode, "tail": (r.stdout + r.stderr)[-300:]}
    return out


def free_gb(path: str = "C:\\") -> float:
    try:
        f = ctypes.c_ulonglong()
        r = ctypes.windll.kernel32.GetDiskFreeSpaceExW(
            path, None, None, ctypes.byref(f))
        return round(f.value / (1 << 30), 1) if r else -1.0
    except Exception:
        return -1.0


def build_task_table(repo: str, skip_build: bool) -> list[dict]:
    pyw = os.path.join(repo, "venv-winml", "Scripts", "python.exe")
    pyc = os.path.join(repo, "venv-cpu", "Scripts", "python.exe")
    ph = lambda n: os.path.join(repo, "runner", "phases", n)
    T = []
    T.append({"key": "m0-amdgpu", "heavy": False, "est_gb": 0.6,
              "cmd": [pyw, ph("m0_ep_discovery.py"), "--repo", repo, "--ep", "AMDGPUExecutionProvider",
                      "--download", "yes"], "timeout": 45 * 60})
    T.append({"key": "m0-migraphx", "heavy": False, "est_gb": 0.6,
              "cmd": [pyw, ph("m0_ep_discovery.py"), "--repo", repo, "--ep", "MIGraphXExecutionProvider",
                      "--download", "yes"], "timeout": 45 * 60})
    T.append({"key": "m1-operators", "heavy": False, "est_gb": 0.0,
              "cmd": [pyw, ph("m1_operators.py"), "--repo", repo], "timeout": 90 * 60})
    T.append({"key": "m1-multiout", "heavy": False, "est_gb": 0.0,
              "cmd": [pyw, ph("m1_multi_output.py"), "--repo", repo], "timeout": 60 * 60})
    T.append({"key": "m1-cache", "heavy": False, "est_gb": 0.0,
              "cmd": [pyw, ph("m1_cache_canary.py"), "--repo", repo], "timeout": 30 * 60})
    T.append({"key": "m2-models", "heavy": False, "est_gb": 1.2,
              "cmd": [pyw, ph("m2_models.py"), "--repo", repo], "timeout": 3 * 60 * 60})
    T.append({"key": "m3-perf", "heavy": True, "est_gb": 0.0,
              "cmd": [pyw, ph("m3_perf.py"), "--repo", repo], "timeout": 2 * 60 * 60})
    T.append({"key": "m3-stability", "heavy": True, "est_gb": 0.0,
              "cmd": [pyw, ph("m3_stability.py"), "--repo", repo], "timeout": 2 * 60 * 60})
    T.append({"key": "m4-feasibility", "heavy": False, "est_gb": 0.05,
              "cmd": [pyw, ph("m4_build_feasibility.py"), "--repo", repo], "timeout": 45 * 60})
    T.append({"key": "m5-diagnostics", "heavy": False, "est_gb": 0.0,
              "cmd": [pyw, ph("m5_diagnostics.py"), "--repo", repo], "timeout": 60 * 60})
    T.append({"key": "wrapup", "heavy": False, "est_gb": 0.0, "always_last": True,
              "cmd": [pyw, ph("wrapup.py"), "--repo", repo], "timeout": 20 * 60})
    if skip_build:
        T = [t for t in T if not t["key"].startswith("m4-build")]
    return T


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--deadline", default="2026-11-17T08:30:00")
    ap.add_argument("--max-download-gb", type=float, default=15.0)
    ap.add_argument("--max-disk-gb", type=float, default=35.0)
    ap.add_argument("--skip-build", action="store_true")
    ap.add_argument("--resume-only", action="store_true",
                    help="run pending tasks once, then exit (for resume.ps1)")
    args = ap.parse_args()
    repo = os.path.abspath(args.repo)
    led_path = os.path.join(repo, "reports", "ledger.json")
    deadline = datetime.datetime.fromisoformat(args.deadline)
    logs_dir = os.path.join(repo, "reports", "supervisor_logs")
    os.makedirs(logs_dir, exist_ok=True)

    state = {"phase": "startup", "task": None, "alive": True}
    hb(repo, state)

    led = L.load_ledger(led_path)
    tasks_state = led.setdefault("supervisor_tasks", {})
    dl_used = sum(t.get("est_gb_used", 0) for t in tasks_state.values())
    crash_counts: dict[str, int] = {}
    last_commit = 0.0

    with StayAwake():
        T = build_task_table(repo, args.skip_build)
        wrapup_task = [t for t in T if t.get("always_last")]
        normal = [t for t in T if not t.get("always_last")]
        for task in normal:
            now = datetime.datetime.now()
            remaining_min = (deadline - now).total_seconds() / 60.0
            if remaining_min <= 0:
                state["phase"] = "deadline_reached"
                hb(repo, state)
                break
            key = task["key"]
            rec = tasks_state.get(key, {})
            if rec.get("status") in ("DONE", "DANGEROUS_STOP", "SKIP"):
                continue
            if crash_counts.get(key, 0) >= 3:
                tasks_state[key] = {"status": "DANGEROUS_STOP",
                                    "note": "3 consecutive native crashes"}
                L.upsert_case(led_path, {"id": f"supervisor/{key}", "phase": "supervisor",
                                         "status": "DANGEROUS_STOP",
                                         "reason": "3 consecutive native crashes"})
                continue
            if task.get("heavy") and remaining_min < 25:
                tasks_state[key] = {"status": "SKIP", "note": "deadline guard (25min)"}
                continue
            free = free_gb()
            if free < args.max_disk_gb and task.get("heavy"):
                tasks_state[key] = {"status": "SKIP", "note": f"disk low {free}GB"}
                continue
            if dl_used + task.get("est_gb", 0) > args.max_download_gb:
                tasks_state[key] = {"status": "SKIP", "note": "download budget"}
                continue

            state.update({"phase": "running", "task": key})
            hb(repo, state)
            log_path = os.path.join(logs_dir, key + ".log")
            res = sh(task["cmd"], task["timeout"], repo, log_path)
            rc = res["exit_code"]
            crash = rc in CRASH_CODES or rc in (-999,)  # timeout or native crash -> count
            if crash:
                crash_counts[key] = crash_counts.get(key, 0) + 1
            tasks_state[key] = {"status": "DONE" if rc == 0 else ("CRASH" if rc in CRASH_CODES else "FAIL"),
                                "exit_code": rc, "attempts": rec.get("attempts", 0) + 1,
                                "seconds": res["seconds"], "log": os.path.relpath(res["log"], repo),
                                "est_gb_used": task.get("est_gb", 0) if rc == 0 else 0}
            if tasks_state[key]["status"] == "DONE":
                dl_used += task.get("est_gb", 0)
            L.upsert_case(led_path, {"id": f"supervisor/{key}", "phase": "supervisor",
                                     "status": "DONE" if rc == 0 else ("CRASH_NATIVE" if rc in CRASH_CODES else ("TIMEOUT" if rc == -999 else "FAIL")),
                                     "exit_code": rc, "seconds": res["seconds"]})
            state.update({"phase": "idle", "task": f"{key}:{rc}"})
            hb(repo, state)

            # checkpoint commit every 30 min of active time
            if time.time() - last_commit > 1800:
                try:
                    git_checkpoint(repo, push=True)
                except Exception:
                    pass
                last_commit = time.time()

        # wrap-up (also on deadline)
        for w in wrapup_task:
            rec = tasks_state.get(w["key"], {})
            if rec.get("status") == "DONE":
                continue
            state.update({"phase": "wrapup", "task": w["key"]})
            hb(repo, state)
            res = sh(w["cmd"], w["timeout"], repo,
                     os.path.join(logs_dir, w["key"] + ".log"))
            tasks_state[w["key"]] = {"status": "DONE" if res["exit_code"] == 0 else "FAIL",
                                     "exit_code": res["exit_code"], "seconds": res["seconds"]}
            L.upsert_case(led_path, {"id": f"supervisor/{w['key']}", "phase": "supervisor",
                                     "status": "DONE" if res["exit_code"] == 0 else "FAIL",
                                     "exit_code": res["exit_code"]})

    state.update({"phase": "finished", "alive": False})
    hb(repo, state)
    try:
        git_checkpoint(repo, push=True)
    except Exception as e:
        print("final commit failed:", e, file=sys.stderr)
    print("SUPERVISOR_FINISHED", flush=True)


if __name__ == "__main__":
    main()
