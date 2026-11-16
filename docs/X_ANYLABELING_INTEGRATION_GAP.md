# X-AnyLabeling → Windows MIGraphX/AMDGPU EP 集成差距分析

基于 X-AnyLabeling main 分支架构（CVHub520/X-AnyLabeling，本夜未向其提交任何 PR）
与本仓库真机证据。目标：指出迁移到 Windows ML AMDGPU/MIGraphX EP 时的实际缝隙。

## 1. 现状盘点（按模型类别）

| 类别 | 入口 | Session 创建方式 | 迁移注意 |
|---|---|---|---|
| YOLO 检测/分割/姿态/OBB (v5/v8/v11/26) | `OnnxBaseModel` 统一基类 | 自建 `InferenceSession`，providers 由参数注入 | CPU 兜底路径完备；GPU EP 失败会抛异常（好） |
| SAM/SAM2 | `SamOnnxModel`/`EdgeSAMOnnxModel` 等 | encoder/decoder 各自 Session | 多 Session 生命周期；EP 崩溃需进程级隔离 |
| SCRFD/RF-DETR/RT-DETR | 专用 adapter | 自建 Session，常为**多输出**图 | 见下 #150 风险 |
| OCR (PaddleOCR 系) | 两阶段 detector+recognizer | 独立 Session ×2 | 交互重复推理；EP 缓存开关需 canary（#117） |
| 分类/深度估计 | `OnnxBaseModel` 变体 | 同上 | — |

## 2. 与本夜真机发现的对应缝隙

1. **providers 注入方式必须换 API**：X-AnyLabeling 目前用经典
   `InferenceSession(providers=["...", "CPUExecutionProvider"])`。本夜实测
   （`reports/m5/probe151_*.json`）经典方式在插件 EP 未注册/不可用时
   **静默丢弃 GPU EP**（session 建成、`get_providers()` 只剩 CPU、无异常）。
   → 迁移到 Windows ML 插件 EP 必须改为：
   `ort.register_execution_provider_library(name, dll)` →
   `get_ep_devices()` 过滤 → `so.add_provider_for_devices([单个设备], {})`，
   并在每次 `run()` 后校验 `get_providers()` 与 profile 的 provider 归属。
2. **多设备注册会炸**：`add_provider_for_devices` 传入 >1 个设备时 AMD
   `amdgpu-ep.dll` 工厂报
   `INVALID_ARGUMENT : only supports selection for a single device`，随后
   **session 级静默回退 CPU**（本夜 run4 日志）。X-AnyLabeling 的设备枚举
   必须去重且只选一个 AMD 设备。
3. **驱动门槛**：EP Framework 1.8.65 在本机（7900 XTX、驱动 32.0.21030.2001 /
   2025-09-25）上 `hipSetDevice` 即 `HIP failure 101: invalid device ordinal`
   （`reports/m0/*_final.log`）。官方文档要求 MIGraphX EP 驱动 ≥ 25.10.13.09。
   → X-AnyLabeling 需要在启动时做「EP 可用性 canary 图」探测并把失败显式
   告知用户，而不是回退 CPU 后让用户以为在用 GPU。
4. **多输出正确性（#150）**：SCRFD 九输出、SAM 多输出都依赖 by-name 输出映射。
   建议接入本仓库 `runner/common.py::parity_check`（逐输出 name/dtype/shape/值）
   作为 X-AnyLabeling 的回归门禁。
5. **线程/生命周期**：X-AnyLabeling 在 UI 线程反复创建/销毁 Session（模型切换）。
   本夜 M3 soak 的 session churn 用例即按此设计；EP DLL 进程内注册是全局态，
   `unregister_execution_provider_library` 的时机需要与模型切换对齐，避免
   重复注册泄漏。
6. **包依赖**：EP MSIX（Microsoft.WinML.AMD.GPU.EP.Framework.2）由 Windows ML
   目录按需安装（~500MB ROCm 栈）。桌面应用不能假定其存在——首次运行需走
   `WinMLEpEnsureReady`（C API 可从 NuGet 2.5.96 获得，见 docs/PROVENANCE.md）。

## 3. 最小兼容补丁方案（不在本夜提交）

1. `onnx_base.py`：新增 `_ensure_plugin_ep(ep_name)`——注册库→单设备选择→
   canary 推理（Identity 图）→ 失败抛 `EPUnavailableError` 并提示驱动要求。
2. `InferenceSession` 构造统一走 `so.add_provider_for_devices`；删除经典
   providers 注入路径（或仅作 CPU 显式请求）。
3. `run()` 包装器：可选断言 `self.session.get_providers()` 首位为目标 EP
   （debug 模式核对 profile gpu_nodes>0）。
4. 回归：`fixtures/nine_out.onnx` + `two_out_same_shape.onnx` 纳入 CI。
