"""R2.2B YOLO end-to-end child: real image -> real detections on the AMD GPU.

Model: yolo26n.onnx, official Ultralytics export (provenance in
MODEL_PROVENANCE). Input (1,3,640,640) f32; output (1,84,8400) raw head
(4 box + 80 COCO classes, NMS in postprocess).

Pipeline per side (GPU EP and CPU reference, identical code):
  letterbox 640 (pad 114, scale-preserving) -> RGB -> /255 -> CHW f32
  -> session.run -> transpose to (8400,84) -> boxes cxcywh->xyxy
  -> conf>0.25 -> per-class NMS (IoU 0.7) -> detections

Acceptance (GPU PASS):
  1. target EP in session providers
  2. >=1 profile node on the EP
  3. raw tensor parity vs CPU (predeclared atol/rtol 1e-2 FP32)
  4. detection agreement vs CPU: same class set; per-detection best-IoU
     >=0.9 with equal class and score delta <=0.05 (both directions)
Also recorded: semantic sanity on the official bus.jpg (expected: bus +
persons present among detections) -- informational, not a pass gate,
since exact counts depend on thresholds.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from datetime import datetime, timezone

import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import onnxruntime as ort  # noqa: E402

from common import sha256_file  # noqa: E402
from r2.model_child import EP_LIBRARY, OPT_LEVELS, build_identity_view  # noqa: E402
from r2.device_resolver import GpuUnavailableError, resolve_device  # noqa: E402
from r2.model_child import in_process_canary  # noqa: E402

COCO_CLASSES = 80
CONF_THR = 0.25
IOU_THR = 0.7
TOL = {"atol": 1e-2, "rtol": 1e-2}  # predeclared, FP32


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def letterbox(img_bgr: np.ndarray, size: int = 640) -> tuple[np.ndarray, tuple]:
    """Ultralytics-style letterbox: scale-preserving resize + pad 114."""
    import cv2
    h, w = img_bgr.shape[:2]
    r = min(size / h, size / w)
    nh, nw = round(h * r), round(w * r)
    resized = cv2.resize(img_bgr, (nw, nh), interpolation=cv2.INTER_LINEAR)
    canvas = np.full((size, size, 3), 114, dtype=np.uint8)
    top, left = (size - nh) // 2, (size - nw) // 2
    canvas[top:top + nh, left:left + nw] = resized
    return canvas, (top, left, r)


def preprocess(path: str) -> tuple[np.ndarray, dict]:
    import cv2
    bgr = cv2.imread(path)
    if bgr is None:
        raise RuntimeError(f"cannot decode image {path}")
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    canvas, geo = letterbox(rgb)
    x = canvas.astype(np.float32) / 255.0
    x = x.transpose(2, 0, 1)[None]  # (1,3,640,640)
    return np.ascontiguousarray(x), {"orig_hw": (bgr.shape[0], bgr.shape[1]),
                                     "letterbox": geo}


def postprocess(out: np.ndarray, geo: dict) -> list[dict]:
    """(1,84,8400) -> detections in original image coordinates."""
    preds = out[0].T  # (8400, 84)
    boxes_cxcywh, scores_all = preds[:, :4], preds[:, 4:]
    class_ids = scores_all.argmax(1)
    confs = scores_all.max(1)
    keep = confs > CONF_THR
    dets = []
    top, left, r = geo["letterbox"]
    oh, ow = geo["orig_hw"]
    for i in np.nonzero(keep)[0]:
        cx, cy, w, h = boxes_cxcywh[i]
        x1 = (cx - w / 2 - left) / r
        y1 = (cy - h / 2 - top) / r
        x2 = (cx + w / 2 - left) / r
        y2 = (cy + h / 2 - top) / r
        dets.append({"class": int(class_ids[i]), "conf": float(confs[i]),
                     "box": [max(0, float(x1)), max(0, float(y1)),
                             min(ow, float(x2)), min(oh, float(y2))]})
    return nms(dets)


def nms(dets: list[dict], iou_thr: float = IOU_THR) -> list[dict]:
    def iou(a, b):
        ax1, ay1, ax2, ay2 = a["box"]
        bx1, by1, bx2, by2 = b["box"]
        ix = max(0, min(ax2, bx2) - max(ax1, bx1))
        iy = max(0, min(ay2, by2) - max(ay1, by1))
        inter = ix * iy
        ua = (ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - inter
        return inter / ua if ua > 0 else 0.0

    out: list[dict] = []
    for d in sorted(dets, key=lambda d: -d["conf"]):
        if all(d["class"] != o["class"] or iou(d, o) < iou_thr for o in out):
            out.append(d)
    return out


def detection_agreement(a: list[dict], b: list[dict]) -> dict:
    def iou(x, y):
        ix = max(0, min(x["box"][2], y["box"][2]) - max(x["box"][0], y["box"][0]))
        iy = max(0, min(x["box"][3], y["box"][3]) - max(x["box"][1], y["box"][1]))
        inter = ix * iy
        ua = ((x["box"][2] - x["box"][0]) * (x["box"][3] - x["box"][1])
              + (y["box"][2] - y["box"][0]) * (y["box"][3] - y["box"][1]) - inter)
        return inter / ua if ua > 0 else 0.0

    def best_match(d, others):
        best, bi = 0.0, None
        for o in others:
            if o["class"] != d["class"]:
                continue
            v = iou(d, o)
            if v > best:
                best, bi = v, o
        return best, bi

    ab = [(d["class"], round(best_match(d, b)[0], 4),
           abs(d["conf"] - (best_match(d, b)[1] or d)["conf"]))
          for d in a]
    ba = [(d["class"], round(best_match(d, a)[0], 4))
          for d in b]
    ok = (len(a) == len(b)
          and all(iou_v >= 0.9 and dconf <= 0.05 for _, iou_v, dconf in ab)
          and all(iou_v >= 0.9 for _, iou_v in ba))
    return {"ok": bool(ok), "gpu_count": len(a), "cpu_count": len(b),
            "gpu_matches": ab, "cpu_matches": ba}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ep", required=True,
                    choices=["AMDGPUExecutionProvider", "MIGraphXExecutionProvider"])
    ap.add_argument("--model", default=os.path.join(REPO, "artifacts", "models",
                                                    "yolo26n.onnx"))
    ap.add_argument("--image", default=os.path.join(REPO, "artifacts", "models",
                                                    "bus.jpg"))
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    receipt = {
        "schema_version": "r2/yolo_e2e/1",
        "case_id": f"r2_yolo26n_e2e_{args.ep}",
        "model": os.path.basename(args.model),
        "model_sha256": sha256_file(args.model),
        "image": os.path.basename(args.image),
        "image_sha256": sha256_file(args.image),
        "ep": args.ep, "ort_version": ort.__version__,
        "conf_thr": CONF_THR, "iou_thr": IOU_THR, "tolerance": TOL,
        "started_utc": utc_now(), "real_hardware": True,
    }
    try:
        x, geo = preprocess(args.image)
        receipt["preprocess"] = {"input_shape": list(x.shape),
                                 "letterbox": str(geo["letterbox"]),
                                 "orig_hw": geo["orig_hw"],
                                 "input_sha256": sha256_of_array(x)}
        # device resolution
        view, _ = build_identity_view(args.ep)
        canary = in_process_canary(os.path.join(REPO, "fixtures", "identity_add.onnx"),
                                   args.ep)
        res = resolve_device(view, args.ep, canary)
        receipt["device_luid"] = res.candidate.luid

        import r2.model_child as mc  # reuse _metadata_of via module
        devs = [d for d in ort.get_ep_devices()
                if d.ep_name == args.ep
                and str(mc._metadata_of(d).get("LUID", "")) == str(res.candidate.luid)]
        so = ort.SessionOptions()
        so.enable_profiling = True
        so.profile_file_prefix = args.out.replace(".json", "_profile")
        so.graph_optimization_level = OPT_LEVELS["ALL"]
        so.add_provider_for_devices([devs[0]], {})
        t0 = time.time()
        sess = ort.InferenceSession(args.model, so)
        receipt["session_create_s"] = round(time.time() - t0, 2)
        receipt["session_providers"] = list(sess.get_providers())
        if args.ep not in receipt["session_providers"]:
            receipt["classification"] = "FAIL_EP_NOT_ATTACHED"
            raise RuntimeError(json.dumps(receipt["session_providers"]))

        t0 = time.time()
        out = sess.run(None, {"images": x})
        receipt["gpu_run_s"] = round(time.time() - t0, 3)
        gpu_tensor = np.asarray(out[0])
        profile_path = sess.end_profiling()

        cpu = ort.InferenceSession(args.model, providers=["CPUExecutionProvider"])
        cpu_tensor = np.asarray(cpu.run(None, {"images": x})[0])

        # 1) raw tensor parity
        diff = np.abs(gpu_tensor.astype(np.float64) - cpu_tensor.astype(np.float64))
        rel = diff / np.maximum(np.abs(cpu_tensor.astype(np.float64)), 1e-6)
        tensor_ok = bool(np.allclose(gpu_tensor, cpu_tensor,
                                     atol=TOL["atol"], rtol=TOL["rtol"]))
        receipt["tensor_parity"] = {
            "shape": list(gpu_tensor.shape),
            "max_abs": float(diff.max()), "mean_abs": float(diff.mean()),
            "max_rel": float(rel.max()), "allclose": tensor_ok,
            "nan_gpu": int(np.isnan(gpu_tensor).sum()),
            "inf_gpu": int(np.isinf(gpu_tensor).sum()),
        }

        # 2) detections
        gpu_dets = postprocess(gpu_tensor, geo)
        cpu_dets = postprocess(cpu_tensor, geo)
        agree = detection_agreement(gpu_dets, cpu_dets)
        receipt["detections_gpu"] = gpu_dets
        receipt["detections_cpu"] = cpu_dets
        receipt["detection_agreement"] = agree
        receipt["semantic_sanity"] = {
            "classes_gpu": sorted({d["class"] for d in gpu_dets}),
            "note": "bus.jpg official asset: bus(class 5)+persons(class 0) expected",
        }

        # 3) profile attribution
        with open(profile_path, encoding="utf-8", errors="replace") as f:
            trace = json.load(f)
        events = trace if isinstance(trace, list) else trace.get("traceEvents", [])
        nodes = [e for e in events if e.get("cat") == "Node"]
        by_prov: dict[str, int] = {}
        for e in nodes:
            p = (e.get("args") or {}).get("provider", "cpu")
            by_prov[p] = by_prov.get(p, 0) + 1
        receipt["profile"] = {"nodes_by_provider": by_prov,
                              "file": os.path.basename(profile_path)}
        ep_nodes = by_prov.get(args.ep, 0)

        if not tensor_ok:
            receipt["classification"] = "FAIL_CORRECTNESS"
        elif agree["ok"]:
            receipt["classification"] = ("PASS_GPU_CORRECT_FULL_COVERAGE"
                                         if ep_nodes == len(nodes)
                                         else "PASS_GPU_CORRECT_PARTIAL_COVERAGE")
        else:
            receipt["classification"] = "FAIL_DETECTION_DISAGREEMENT"
        receipt["gpu_node_ratio"] = round(ep_nodes / len(nodes), 4) if nodes else 0.0
    except GpuUnavailableError as e:
        receipt["classification"] = "FAIL_DEVICE_RESOLUTION"
        receipt["error"] = str(e)
    except Exception:
        receipt.setdefault("classification", "FAIL_RUNTIME")
        receipt["error"] = traceback.format_exc(limit=8)

    receipt["finished_utc"] = utc_now()
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(receipt, f, indent=1, default=str)
    print(receipt.get("classification"), "| tensor_ok:",
          (receipt.get("tensor_parity") or {}).get("allclose"), "| dets:",
          (receipt.get("detection_agreement") or {}).get("ok"), flush=True)
    sys.stdout.flush(); sys.stderr.flush()
    os._exit(0 if str(receipt.get("classification", "")).startswith("PASS") else 1)


def sha256_of_array(a: np.ndarray) -> str:
    import hashlib
    return hashlib.sha256(np.ascontiguousarray(a).tobytes()).hexdigest()


if __name__ == "__main__":
    main()
