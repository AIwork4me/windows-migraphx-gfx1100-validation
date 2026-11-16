# 在干净的 Windows gfx1100 机器上复现（5–10 分钟 smoke）

前提：Windows 11 24H2+（本机 build 26300）、Radeon RX 7900 XTX、管理员不需要。

```powershell
git clone https://github.com/AIwork4me/windows-migraphx-gfx1100-validation.git
cd windows-migraphx-gfx1100-validation

# 1) 环境预检（只读，落盘 reports/preflight/）
powershell -ExecutionPolicy Bypass -File scripts\collect_env.ps1

# 2) 双 venv（相互隔离：Windows ML 路线 vs PyPI CPU 对照）
powershell -ExecutionPolicy Bypass -File scripts\setup-winml.ps1
#    需要 NuGet 包 Microsoft.Windows.AI.MachineLearning 2.5.96 提供 EP 目录 C API：
#    https://www.nuget.org/packages/Microsoft.Windows.AI.MachineLearning/2.5.96
#    解包后 runtimes/win-x64/native 即本仓库 ctypes 桥所用 DLL 路径。

# 3) EP 目录枚举 + 官方 AMD EP 下载尝试 + 最小图端到端（M0）
venv-winml\Scripts\python.exe runner\phases\m0_ep_discovery.py --repo . --ep AMDGPUExecutionProvider --download yes

# 4) 算子/多输出/缓存与 #151 探针（M1/M5）
venv-winml\Scripts\python.exe runner\phases\m1_operators.py --repo .
venv-winml\Scripts\python.exe runner\phases\m1_multi_output.py --repo .
venv-winml\Scripts\python.exe runner\phases\m5_diagnostics.py --repo .

# 5) 状态
powershell -ExecutionPolicy Bypass -File scripts\status.ps1
```

## 复现本夜的关键 FAIL/BLOCKED

1. **AMD EP 下载失败**（如你的网络与 WU CDN 不通）：
   `WinMLEpEnsureReady` 返回 `0x800705B4`（超时）→ 复查
   `Get-WinEvent -LogName Microsoft-Windows-AppXDeploymentServer/Operational`，
   应见 `0x80073CF8`（无法为 `x-windowsupdate://...` 获取暂存会话）。
   证据：`reports/m0/m0_AMDGPUExecutionProvider_run.json`。
2. **#151 静默 CPU 回退**：`reports/m5/probe151_AMDGPUExecutionProvider.json`
   —— `providers=["AMDGPUExecutionProvider","CPUExecutionProvider"]` 在 EP 未注册时
   0.01s 建 session 成功、`get_providers()` 只剩 CPU、推理照常完成（无异常、无提示）。
3. **WinRT catalog 类未注册**：pywinrt `ExecutionProviderCatalog.get_default()` 抛
   `REGDB_E_CLASSNOTREG`（本机 WindowsAppRuntime 2.6.4 已装但无 ML 运行时注册）；
   解决路径 = NuGet C API（本仓库方案）。

## 全量过夜复跑

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_overnight.ps1 -DeadlineLocal "2026-11-18 08:30:00"
# 断线后续跑（幂等，跳过 ledger 中 terminal 的 case）：
powershell -ExecutionPolicy Bypass -File scripts\resume.ps1
```
