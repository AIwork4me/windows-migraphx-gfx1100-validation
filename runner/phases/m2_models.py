"""M2: real-world ONNX models from official sources (CPU parity + placement).

Models (all official ONNX Model Zoo, Apache-2.0 unless noted; sha256 recorded,
weights NOT committed to git - see .gitignore):
  squeezenet1.0-12, mobilenetv2-12, mnist-8, emotion-ferplus-8 (MIT),
  ssd_mobilenet_v1_10 (multi-output real model)
"""
from __future__ import annotations
import argparse, os, sys, json, urllib.request, hashlib
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
import common as C
import ledger as L
from ep_runner import run_case, register_ep, find_winml_ep_library

MODELS = {
    "mnist-8": ("https://github.com/onnx/models/raw/main/validated/vision/classification/mnist/model/mnist-8.onnx", "Apache-2.0"),
    "squeezenet1.0-12": ("https://github.com/onnx/models/raw/main/validated/vision/classification/squeezenet/model/squeezenet1.0-12.onnx", "Apache-2.0"),
    "mobilenetv2-12": ("https://github.com/onnx/models/raw/main/validated/vision/classification/mobilenetv2/model/mobilenetv2-12.onnx", "Apache-2.0"),
    "emotion-ferplus-8": ("https://github.com/onnx/models/raw/main/validated/vision/body_analysis/emotion_ferplus/model/emotion-ferplus-8.onnx", "MIT"),
    "ssd_mobilenet_v1_10": ("https://github.com/onnx/models/raw/main/validated/vision/object_detection_segmentation/ssd-mobilenetv1/model/ssd_mobilenet_v1_10.onnx", "Apache-2.0"),
}
EPS = ["AMDGPUExecutionProvider", "MIGraphXExecutionProvider", "DmlExecutionProvider"]


def fetch(url: str, dest: str) -> str:
    if os.path.exists(dest) and os.path.getsize(dest) > 1024:
        return dest
    print("downloading", url, flush=True)
    urllib.request.urlretrieve(url, dest + ".part")
    os.replace(dest + ".part", dest)
    return dest


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=C.REPO)
    ap.add_argument("--max-mb", type=int, default=40)
    args = ap.parse_args()
    repo = os.path.abspath(args.repo)
    mdir = os.path.join(repo, "artifacts", "models")
    os.makedirs(mdir, exist_ok=True)
    led = os.path.join(repo, "reports", "ledger.json")
    receipt = C.new_receipt("m2", "m2_models_run")
    receipt["models"] = {}
    for ep in EPS:
        register_ep(ep, [find_winml_ep_library(ep)])
        for name, (url, lic) in MODELS.items():
            dest = os.path.join(mdir, name + ".onnx")
            cid_dl = f"m2/download/{name}"
            try:
                fetch(url, dest)
                size_mb = os.path.getsize(dest) / (1 << 20)
                sha = C.sha256_file(dest)
                receipt["models"][name] = {"url": url, "license": lic,
                                           "sha256": sha, "size_mb": round(size_mb, 1)}
                L.upsert_case(led, {"id": cid_dl, "phase": "m2", "status": "PASS",
                                    "sha256": sha, "license": lic, "size_mb": round(size_mb, 1)})
                if size_mb > args.max_mb:
                    L.upsert_case(led, {"id": f"m2/{ep}/{name}/b1", "phase": "m2",
                                        "status": "SKIP_NOT_APPLICABLE",
                                        "reason": f"model too large ({size_mb:.0f}MB > {args.max_mb}MB budget)"})
                    continue
            except Exception as e:
                L.upsert_case(led, {"id": cid_dl, "phase": "m2", "status": "FAIL",
                                    "reason": repr(e)[:300]})
                continue
            meta = C.meta_from_model(dest)
            for b in (1, 2):
                case = run_case(repo, "m2", ep, dest, meta, b,
                                f"m2/{ep}/{name}/b{b}", warmup=2, timed=1)
                print(case["id"], case.get("status"), flush=True)
    C.save_receipt("m2", receipt)
    print("M2_MODELS_DONE")


if __name__ == "__main__":
    rc = main()
    sys.stdout.flush(); sys.stderr.flush()
    os._exit(rc if isinstance(rc, int) else 0)  # hard exit: skip interpreter shutdown (WinML/ORT leave non-daemon threads)
