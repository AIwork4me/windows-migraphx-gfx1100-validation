"""#151 probe child: classic providers=[EP] on an unregistered plugin EP.

Writes its JSON result to reports/m5/probe151_<ep>.json and hard-exits
(ORT/WinML leave non-daemon threads; interpreter shutdown can hang).
"""
import json, os, sys, time

repo = os.path.abspath(sys.argv[1])
ep = sys.argv[2]

sys.path.insert(0, os.path.join(repo, "runner"))
import common as C  # noqa: E402
import onnxruntime as ort  # noqa: E402

man = C.write_fixtures()
path = os.path.join(repo, "fixtures", "identity_add.onnx")
feeds = C.make_feed("identity_add", man["identity_add"], 1)
t0 = time.time()
res = {"ep": ep, "mode": "classic-providers-list"}
try:
    so = ort.SessionOptions()
    so.enable_profiling = True
    sess = ort.InferenceSession(path, sess_options=so,
                                providers=[ep, "CPUExecutionProvider"])
    res["session_create_s"] = round(time.time() - t0, 2)
    res["get_providers"] = list(sess.get_providers())
    sess.run(None, feeds)
    res["run"] = "ok"
    sess.run(None, feeds)
    prof = sess.end_profiling()
    res["placement"] = C.analyze_profile(prof, ep)
    res["stage"] = "completed"
except Exception as e:
    res["stage"] = "exception"
    res["exception"] = repr(e)[:400]
out = os.path.join(repo, "reports", "m5", f"probe151_{ep}.json")
os.makedirs(os.path.dirname(out), exist_ok=True)
with open(out, "w", encoding="utf-8") as f:
    json.dump(res, f, ensure_ascii=False, indent=1)
sys.stdout.flush(); sys.stderr.flush()
os._exit(0)  # hard exit: skip interpreter shutdown (WinML/ORT non-daemon threads)
