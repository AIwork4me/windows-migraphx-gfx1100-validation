# Provenance / 数据来源与许可

## 上游仓库（pinned SHAs，浅克隆于本机 deps/，不入库）

| 上游 | URL | HEAD SHA（2026-11-17 克隆） |
|---|---|---|
| onnxruntime/onnxruntime-ep-amdgpu | https://github.com/onnxruntime/onnxruntime-ep-amdgpu | `77bba054fbaa31091f4d01d5dda490b5caae1150` |
| ROCm/AMDMIGraphX | https://github.com/ROCm/AMDMIGraphX | `d34c354238bd3f4cdd094d994564d58d9f823581` |
| microsoft/onnxruntime | https://github.com/microsoft/onnxruntime | `368a6729835340f7ee7defbd52ee7ec95883d3dd` |

## 软件组件

| 组件 | 版本 | 来源 | 许可 |
|---|---|---|---|
| onnxruntime-windowsml (PyPI) | 1.30.1.202610302006 | pypi.org | MIT |
| onnxruntime (PyPI, CPU 对照 venv) | 1.32.0 | pypi.org | MIT |
| winrt-Microsoft.Windows.AI.MachineLearning | 4!2.6.5 | pypi.org | MIT (pywinrt) |
| Microsoft.Windows.AI.MachineLearning (NuGet) | 2.5.96 | nuget.org（经 nuget.azure.cn 镜像重定向） | Microsoft（含 ThirdPartyNotices） |
| Python | 3.12.10 x64 (winget, per-user) | python.org | PSF |
| GPU 驱动 | 32.0.21030.2001 (2025-09-25) | 已装（未改动） | — |

说明：System32 的 `amdhip64_6.dll`（FileVersion 10.0.3652.0）由 AMD 显卡驱动携带，
**不是** ROCm 10.1 HIP SDK；本机无 hipcc/头文件/导入库。

## 模型

- `fixtures/*.onnx`：本仓库原创合成图（MIT，见 LICENSE），manifest 含 SHA256。
- M2 真实模型（若执行）：ONNX Model Zoo 官方（Apache-2.0；emotion-ferplus 为 MIT），
  仅存于本机 `artifacts/models/`，**权重不 push**（.gitignore 排除），receipt 记录
  URL+SHA256。

## Windows ML EP 目录数据

- 通过 NuGet 2.5.96 的官方 C API（`WinMLEpCatalog.h`）在本机枚举；本机被公告的
  EP：AMDGPUExecutionProvider / MIGraphXExecutionProvider / OpenVINOExecutionProvider /
  WebGpuExecutionProvider（全部 NotPresent）。MIGraphX EP 在 Windows ML 2.x 已被
  Microsoft 标记为 Deprecated（见 learn.microsoft.com，2026-11-13 版页面）。
