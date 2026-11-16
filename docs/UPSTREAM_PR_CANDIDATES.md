# Upstream PR 候选（本夜不提交，仅列证据与最小修复范围）

## P1 — onnxruntime/onnxruntime-ep-amdgpu：Windows HIP 设备枚举失败的清晰报错

- **问题**：驱动不满足要求时（本机 32.0.21030.2001，2025-09-25），
  `amdgpu-ep.dll` 在 `hip\allocator.cc:14 hipSetDevice(device_id)` 处抛
  `HIP failure 101: invalid device ordinal; GPU=0`。用户视角是「装好了 EP
  但永远跑不了 GPU」，无前置检查、无驱动版本提示。
- **证据**：`reports/m0/m0_AMDGPUExecutionProvider_final.log`、
  `m0_MIGraphXExecutionProvider_final.log`、receipt JSON（每 case 的 exception）。
- **最小修复**：EP 工厂 CreateProvider 入口先 `hipGetDeviceCount()`+驱动版本
  查询，返回带明确文案的 `ORT_EP_DEVICE_NOT_COMPATIBLE`（含最低驱动
  25.10.13.09 指引），避免进入 allocator 才失败。
- **回归**：本仓库 M0 脚本即最小重现器。
- **风险**：低（错误路径强化）；需 maintainer 确认错误码策略。

## P2 — onnxruntime（plugin EP python 绑定）：多设备注册的静默 CPU 回退

- **问题**：`SessionOptions.add_provider_for_devices([dev1, dev2], {})` 对 AMD
  EP 报 `only supports selection for a single device` 后，session 级
  **自动 "Falling back to ['CPUExecutionProvider']"**（run4 日志），上层无异常。
  与 #151 同族：静默回退让"GPU 加速"变成假象。
- **证据**：`reports/m0/m0_migraphx_run4.log`（ORC 原文 fallback 横幅）。
- **最小修复**：多设备→EP 工厂失败时把 fallback 降级为可捕获的
  `Fail`/异常，或至少在 `get_providers()` 里保留标记（`__fell_back_from`）。
- **需澄清**：fallback 语义是插件 EP 框架的设计还是缺陷（对应 issue #151）。

## P3 — onnxruntime-ep-amdgpu README：Windows 预编译分发现状（#164）

- **问题**：#164 请求 Windows x64 预编译包。真机证据显示分发**其实存在**但走
  Windows ML EP 目录（MSIX `Microsoft.WinML.AMD.GPU.EP.Framework.2`，
  1.8.65.0，2026-10-28），仓库 README 未提及，开发者无从发现；
  NuGet/CMake 消费者（Microsoft.Windows.AI.MachineLearning 2.5.96 提供
  `WinMLEpCatalog.h` + `register_execution_provider_library(name, path)`）
  也没有文档串联。
- **最小修复**：README 增加 "Windows: consuming the Windows ML EP package"
  一节（EnsureReady → libraryPath → ORT 注册 → 单设备选择），并注明
  驱动最低版本矩阵（AMDGPU vs MIGraphX EP）。
- **证据**：本仓库 docs/REPRODUCE_WINDOWS_GFX1100.md 全链路命令。

## 附：不构成 PR 的观察

- `custom_kernels_gfx1151.dll` 是包内唯一预编译 kernel（Phoenix iGPU）；
  gfx1100 依赖 hiprtc JIT 路径（本夜未到达该路径，因设备枚举先失败）。
- EP 包内自带 `amdhip64_7.dll`（HIP 7 ABI），与 System32 驱动携带的
  `amdhip64_6.dll`（FileVersion 10.0.3652.0）并存——版本协商逻辑值得上游
  文档化。
