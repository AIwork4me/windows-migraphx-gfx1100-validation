# 硬件/软件矩阵（本夜实测环境）

| 项 | 值 | 证据 |
|---|---|---|
| OS | Windows 11 专业版 10.0.26300 (64位) | reports/preflight/os_version.txt |
| CPU | i7-13700K, 16C/24T | preflight/cpu_ram.txt |
| RAM | 31.8 GB | 同上 |
| GPU0 | **AMD Radeon RX 7900 XTX**, PCI VEN_1002 DEV_744C (gfx1100) | preflight/gpu_pnp_hwid.txt |
| GPU1 | Intel UHD 770 (VEN_8086 DEV_A780) | 同上 |
| GPU 驱动 | 32.0.21030.2001 (2025-09-25) | preflight/gpu_adapters.txt |
| 虚拟适配器 | GameViewer IDD + RDP IDD（远程会话） | 同上 |
| 磁盘 | C: 930GB / 剩余 ~569GB | preflight/disk_free.txt |
| Python | 3.12.10 x64（winget per-user，非 Store） | toolchain.txt |
| ORT (WinML) | onnxruntime-windowsml 1.30.1.202610302006 | ledger receipts |
| ORT (CPU 对照) | onnxruntime 1.32.0 | 同上 |
| EP 目录运行时 | Microsoft.Windows.AI.MachineLearning NuGet 2.5.96（C API ctypes 桥） | docs/PROVENANCE.md |
| AMD EP 包 | Microsoft.WinML.AMD.GPU.EP.Framework.2 **1.8.65.0**（MSIX，2026-10-28 版） | reports/m0 receipts |
| HIP 运行时 | System32 amdhip64_6.dll FileVersion 10.0.3652.0（驱动携带）+ EP 包内 amdhip64_7.dll | preflight/hip_dlls.txt |
| 编译工具链 | **无** VS2022/MSVC/CMake/Ninja/Windows SDK/HIP SDK | reports/m4/m4_feasibility_run.json |
| gh / git | gh 2.103.3 (AIwork4me) / git 2.56.0 | preflight/network_github.txt |
| TDR 历史 | 近 14 天无 4101/117/141 事件 | preflight/eventlog_tdr_recent.txt |
