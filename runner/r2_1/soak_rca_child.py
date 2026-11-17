#!/usr/bin/env python3
"""R2.1-C soak RCA child: stage-aware instrumented single (model, EP) loop.

Every stage transition is appended to a JSONL file and fsynced BEFORE the
potentially hanging C API call and again AFTER it:
  spawn, register_ep:enter/leave, resolve_device, create_session:enter/leave,
  warmup, run:enter/leave, run:iter (every log_every iters), recreate:enter/leave,
  session_release:enter/leave, gc_collect:enter/leave, heartbeat,
  end_profiling, finalize, interpreter_exit

The R2 stall (iter 9856, recreate due at 10000) left no stage evidence; this
child makes the boundary observable. One physical-GPU child per supervisor;
never two GPU children at once.
"""
from __future__ import annotations

import argparse
import gc
import json
import os
import sys
import time
import traceback
from datetime import datetime, timezone

import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "runner"))

import onnxruntime as ort  # noqa: E402

from common import sha256_file  # noqa: E402
from r2.model_child import (  # noqa: E402
    OPT_LEVELS, build_identity_view, in_process_canary, session_inputs, _metadata_of,
)
from r2.device_resolver import resolve_device  # noqa: E402

_T0 = time.perf_counter()


def emit(fh, stage, *, iter_=0, rc=None, **extra):
    rec = {
        "t_mono": round(time.perf_counter() - _T0, 3),
        "t_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
        "pid": os.getpid(), "ppid": os.getppid(), "iter": iter_, "stage": stage,
    }
    if rc is not None:
        rec["rc"] = rc
    if extra:
        rec.update(extra)
    fh.write(json.dumps(rec, default=str) + "\n")
    fh.flush()
    os.fsync(fh.fileno())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--ep", required=True)
    ap.add_argument("--minutes", type=float, default=10.0)
    ap.add_argument("--recreate-every", type=int, default=0, help="0 = never recreate")
    ap.add_argument("--log-every", type=int, default=500)
    ap.add_argument("--out", required=True, help="JSONL telemetry path")
    ap.add_argument("--seed", type=int, default=1101)
    ap.add_argument("--fault", default="none",
                    choices=["none", "sleep_prerun", "blocked_recreate",
                             "normal_exit", "nonzero_exit", "blocked_run"])
    ap.add_argument("--teardown", default="none", choices=["none", "explicit"],
                    help="explicit: del sess + gc.collect after run window")
    ap.add_argument("--exit-mode", default="os_exit", choices=["os_exit", "interpreter"])
    args = ap.parse_args()

    fh = open(args.out, "a", encoding="utf-8", newline="\n")
    emit(fh, "spawn", argv_hash=sha256_file(__file__)[:12],
         model=os.path.basename(args.model), ep=args.ep,
         minutes=args.minutes, recreate_every=args.recreate_every,
         fault=args.fault, teardown=args.teardown, exit_mode=args.exit_mode,
         ort=ort.__version__)

    classification = "PASS_SOAK_COMPLETED"
    it = 0
    sess = None
    t_end = time.time() + args.minutes * 60
    t_hb = time.time()

    try:
        # ---- fault injections used to validate the supervisor itself ----
        if args.fault == "sleep_prerun":
            emit(fh, "fault:sleep_prerun")
            time.sleep(10_000)
        if args.fault == "normal_exit":
            emit(fh, "fault:normal_exit")
            fh.close()
            os._exit(0)
        if args.fault == "nonzero_exit":
            emit(fh, "fault:nonzero_exit")
            fh.close()
            os._exit(7)

        emit(fh, "register_ep:enter")
        view, _raw = build_identity_view(args.ep)
        emit(fh, "register_ep:leave")

        emit(fh, "resolve_device:enter")
        canary = in_process_canary(os.path.join(REPO, "fixtures", "identity_add.onnx"),
                                   args.ep)
        res = resolve_device(view, args.ep, canary)
        emit(fh, "resolve_device:leave", luid=str(res.candidate.luid),
             basis=res.basis)

        devs = [d for d in ort.get_ep_devices()
                if d.ep_name == args.ep
                and str(_metadata_of(d).get("LUID", "")) == str(res.candidate.luid)]
        assert devs, "resolved device vanished from ep_devices"

        def make_sess() -> ort.InferenceSession:
            emit(fh, "create_session:enter", iter_=it)
            so = ort.SessionOptions()
            so.graph_optimization_level = OPT_LEVELS["ALL"]
            so.add_provider_for_devices([devs[0]], {})
            s = ort.InferenceSession(args.model, so)
            emit(fh, "create_session:leave", iter_=it,
                 providers=s.get_providers())
            return s

        emit(fh, "create_session:enter", iter_=0)
        so = ort.SessionOptions()
        so.graph_optimization_level = OPT_LEVELS["ALL"]
        so.add_provider_for_devices([devs[0]], {})
        sess = ort.InferenceSession(args.model, so)
        emit(fh, "create_session:leave", iter_=0, providers=sess.get_providers(),
             model_sha256=sha256_file(args.model))

        emit(fh, "warmup:enter")
        feeds0 = session_inputs(sess, 1, model_name=os.path.basename(args.model),
                                input_size=640)
        sess.run(None, feeds0)
        emit(fh, "warmup:leave")

        emit(fh, "run:enter")
        rng = np.random.default_rng(args.seed)
        # deterministic varying input (R2 semantics: seeded per iteration)
        base = np.asarray(feeds0[list(feeds0)[0]], dtype=np.float32)
        recreates = 0
        while time.time() < t_end:
            if args.fault == "blocked_run" and it >= 200:
                emit(fh, "fault:blocked_run", iter_=it)
                time.sleep(10_000)
            wiggle = rng.standard_normal(base.shape).astype(np.float32) * 0.01
            feeds = {name: (base + wiggle if name == list(feeds0)[0] else v)
                     for name, v in feeds0.items()}
            sess.run(None, feeds)
            it += 1
            if it % args.log_every == 0:
                emit(fh, "run:iter", iter_=it)
            now = time.time()
            if now - t_hb >= 30:
                emit(fh, "heartbeat", iter_=it)
                t_hb = now
            if args.recreate_every and it % args.recreate_every == 0:
                emit(fh, "recreate:enter", iter_=it, n=recreates + 1)
                if args.fault == "blocked_recreate" and recreates >= 1:
                    emit(fh, "fault:blocked_recreate", iter_=it)
                    time.sleep(10_000)
                old = sess
                sess = make_sess()  # emits create_session:enter/leave
                emit(fh, "session_release:enter", iter_=it)
                del old
                emit(fh, "session_release:leave", iter_=it)
                recreates += 1
                emit(fh, "recreate:leave", iter_=it, n=recreates)
        emit(fh, "run:leave", iter_=it, iterations=it, recreates=recreates)

        if args.teardown == "explicit":
            emit(fh, "session_release:enter", iter_=it, phase="teardown")
            del sess
            sess = None
            emit(fh, "session_release:leave", iter_=it, phase="teardown")
            emit(fh, "gc_collect:enter", iter_=it, phase="teardown")
            gc.collect()
            emit(fh, "gc_collect:leave", iter_=it, phase="teardown")

        emit(fh, "finalize", iter_=it, iterations=it, recreates=recreates,
             classification=classification)
    except Exception:
        classification = "FAIL_RUNTIME"
        emit(fh, "runtime_error", iter_=it, err=traceback.format_exc(limit=6))
    finally:
        if sess is not None:
            try:
                del sess
            except Exception:
                pass

    emit(fh, "interpreter_exit", iter_=it, classification=classification)
    fh.close()
    if args.exit_mode == "interpreter":
        return 0 if classification.startswith("PASS") else 1
    # default: hard exit; ORT/WinML keeps non-daemon threads that can hang
    # interpreter shutdown (R1 evidence) - the interpreter-exit observation
    # variant is a separate supervisor-managed experiment.
    os._exit(0 if classification.startswith("PASS") else 1)


if __name__ == "__main__":
    sys.exit(main())
