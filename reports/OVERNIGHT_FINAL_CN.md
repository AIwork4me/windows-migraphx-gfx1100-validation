# WIN-MIGRAPHX-GFX1100 过夜真机验证 · 最终报告（中文）

- 运行窗口：2026-11-16 22:54 – 2026-11-17 (UTC+8)，物理主机（非 WSL）
- 仓库：https://github.com/AIwork4me/windows-migraphx-gfx1100-validation （public）
- 证据：`reports/` 下逐 case JSON receipt（命令/退出码/UTC 时间戳/profile/parity），
  ledger.json 为索引；本报告只引用真实落盘数据。

## A. Executive Verdict

| 路线 | 判定 | 一句话依据 |
|---|---|---|
| Windows ML **AMDGPUExecutionProvider** | **CONDITIONAL NO-GO（本机驱动）** | EP 包安装+注册+设备选择全通，但 EP 自带 HIP 运行时 `hipSetDevice` 报 **HIP 101 invalid device ordinal**（驱动 32.0.21030.2001 / 2025-09-25；官方要求 ≥25.10.13.09，且本任务禁改驱动） |
| Windows ML **MIGraphXExecutionProvider** | **CONDITIONAL NO-GO（同上）+ 已被微软标记 Deprecated** | 与 AMDGPU EP 同包（AMD.GPU.EP.Framework 1.8.65.0）同错误；Windows ML 2.x 文档已宣布弃用 |
| **Native ROCm 10.1** Windows 构建 | **BLOCKED_DEPENDENCY** | 无 MSVC/VS2022、无 Windows SDK、无 HIP SDK；三者安装均需管理员权限（未授权）。未编译任何组件，未伪造 DLL |
| **X-AnyLabeling** 就绪度 | **NO-GO（Windows AMD EP 路线，当前驱动）** | 其经典 `providers=[...]` 注入在插件 EP 场景会**静默 CPU 回退**（本夜实测复现 #151）；迁移需改用 device API + canary（docs/X_ANYLABELING_INTEGRATION_GAP.md） |
| 方法学校验（DML 对照） | **PASS（作为对照，不计入 AMD 成功）** | 52 个合成算子 + 8 个真实模型 case：by-name parity 全对 + profile 实证 GPU 计算节点（如 mobilenetv2 300/300 节点在 GPU） |

## B. 代码仓

- URL：https://github.com/AIwork4me/windows-migraphx-gfx1100-validation
- 分支：`main`；最终 HEAD SHA 与 commit 列表见 `reports/manifest.sha256` 生成后的
  最终一节（发布时更新）；push 均经 `git ls-remote` 复核。
- 持续提交：supervisor 每 ~30 分钟 checkpoint commit+push（ledger
  `supervisor_tasks` 记录每次任务状态）。

## C. 物理证据链（关键路径）

1. **硬件**：`reports/preflight/gpu_pnp_hwid.txt` —— `PCI\VEN_1002&DEV_744C`
   （= gfx1100 / RX 7900 XTX），驱动 32.0.21030.2001（2025-09-25）。
2. **EP 广告**：`reports/m0/m0_AMDGPUExecutionProvider_run.json`（catalog C API
   枚举：AMDGPU/MIGraphX/OpenVINO/WebGPU 四 EP 对本机可见，初始 NotPresent）。
3. **EP 下载安装**：首次 EnsureReady 两度 `0x800705B4`+AppX staging
   `0x80073CF8`（WU 内容下载失败，事件日志 `AppXDeploymentServer` 404/403/613 留痕）；
   MIGraphX 路径重试成功 → `Microsoft.WinML.AMD.GPU.EP.Framework.2 1.8.65.0`
   落地（`Get-AppxPackage` 证据 + 包内 26 个 DLL 清单：migraphx.dll、amdhip64_7.dll、
   MIOpen、hiprtc0714、custom_kernels_gfx1151.dll 等）。
4. **注册**：`register_execution_provider_library(name, dll)` 成功；
   `get_ep_devices()` 出现 MIGraphX/AMDGPU 设备（vendor=Advanced Micro Devices）。
5. **真执行尝试**：单设备 `add_provider_for_devices([dev], {})` → AMD EP 工厂真实
   执行 → `hip\allocator.cc:14 hipSetDevice` → **HIP failure 101**
   （`reports/m0/*_final.log` 每 case 异常全文）。**无任何 AMD GPU PASS 被声称。**
6. **#151 静默回退（双 EP 实锤）**：`reports/m5/probe151_*.json` ——
   AMDGPU 与 MIGraphX 两探针均为 SILENT_CPU_FALLBACK：classic providers 注入
   0.01s 建 session、`get_providers()=['CPUExecutionProvider']`、推理完成，
   全程无告警无异常。
7. **多设备陷阱**：run4 日志 —— 传 2 个设备时 EP 报
   `only supports selection for a single device` 后 **session 级静默回退 CPU**
   （"Falling back to ['CPUExecutionProvider']" 横幅原文留痕）。

## D. 矩阵总览（详见 reports/support_matrix.csv）

| 阶段 | AMDGPU EP | MIGraphX EP | DML(对照) | CPU |
|---|---|---|---|---|
| M0 最小图×7×batch1/2/4 | 21 FAIL(HIP101) | 21 FAIL(HIP101) | —（方法学并入 M1/M2） | 全部数值基线 OK |
| M1 算子(34 图×b1/2) | 92 BLOCKED* / FAIL(HIP101) | 88 同左 | **52 PASS_CONTROL** + 12 FAIL(DML 拒载/新算子) + 4 FAIL_CORRECTNESS(log/sqrt 喂值域 bug→已修) | 基线 |
| M1 多输出(#150) | FAIL(HIP101)/BLOCKED | 同左 | 默认优化级 6/6 by-name 正确（含同形交换检测、9 输出）；DISABLE_ALL/BASIC 级 DML 拒载 18 FAIL（Split 算子全级拒载）——真实对照边界，非 AMD 结论 | 正确 |
| M2 真实模型(5 个官方 zoo) | 10 FAIL(HIP101) | 10 FAIL(HIP101) | **8 PASS_CONTROL**（mnist/squeezenet/mobilenetv2/emotion-ferplus）+ 2 FAIL(ssd: ORT/DML `sess.run()` 内 UnicodeDecodeError——上游缺陷证据） | 正确 |
| M3 性能 | 无法测 | 无法测 | conv_relu b1: DML p50 0.086ms vs CPU 0.027ms（微图 DML 开销占优；无夸大） | p50 0.005–0.027ms |
| M3 稳定性 soak | — | — | 30 分钟会话churn+反复推理（receipt 落盘） | — |

*BLOCKED = 该进程未先行 EnsureReady 的运行轮（EP 未注册进程内）；后续轮已统一自动
EnsureReady → 深度为 FAIL(HIP101)。两种都如实保留。

- 假 GPU（CPU-only 冒充）风险：**无** —— 每个 GPU PASS 都有 profile 的
  `gpu_nodes/total_node_events` 与 by-name parity 双门禁。
- 多输出正确性（#150 方向）：DML 对照 3×优化级别×batch 全对；AMD EP 因 HIP
  未能执行，无法证伪 #150 在 MIGraphX 后端的表现（诚实缺口）。

## E. 性能/稳定性

- 仅报告实测：见 D 表与 `reports/performance.csv`（P50/P95/mean、样本数、轮数、
  session 创建、首轮编译时间均在 case receipt 中）。**禁止外推的加速比一律未写。**
- 稳定性：30 分钟 soak（会话创建/销毁 + 批量 sweep + 工作集峰值）——结果以
  `reports/m3/m3_stability_run.json` 为准（wrapup 后更新本行数字）。
- 崩溃分类：本夜**零次**原生崩溃/TDR（preflight 亦确认近 14 天无 TDR）；
  出现过的是（a）WU 下载超时（网络）、（b）HIP 101 立即失败（无挂死）、
  (c) 我们自身的 ledger 自死锁 bug（已修复并记录）。

## F. 原生构建（M4）

- 结论：**BLOCKED_DEPENDENCY，未编译**。首个具体阻塞（按序）：
  1. `cl.exe` 不存在、无 `C:\Program Files\Microsoft Visual Studio`、无 vswhere；
     VS Build Tools 静默安装需管理员（未授权）。
  2. `C:\Program Files (x86)\Windows Kits` 不存在（随 VS 提供）。
  3. 无 HIP SDK（`C:\Program Files\AMD\ROCm` 不存在；安装器需管理员）；
     System32 的 amdhip64_6.dll 为驱动携带运行时，无头文件/导入库，不可充当 SDK。
- 已钉上游：ep-amdgpu@77bba05、AMDMIGraphX@d34c354、onnxruntime@368a672
  （`reports/m4/m4_feasibility_run.json`）。最小修复候选：授权安装 VS Build Tools
  + HIP SDK 10.1 后按 upstream `build.bat --use_migraphx` 起 Release 构建。

## G. 下一步（upstream 机会，均未提交）

1. **ep-amdgpu**：EP 工厂前置 `hipGetDeviceCount` 检查 + 明确驱动版本错误
   （P1，证据最全）。
2. **onnxruntime plugin 绑定**：多设备注册失败时的静默 CPU 回退改为可捕获错误
   （P2，对应 #151 家族）。
3. **ep-amdgpu README**：补「经 Windows ML EP 包消费」文档（P3，对应 #164）。
   需要用户授权后才发 PR。

## H. 复现命令

见 `docs/REPRODUCE_WINDOWS_GFX1100.md` 与 `scripts/reproduce_failure.ps1`
（两处关键失败的最小复现：HIP 101 与 #151 静默回退）。

---
*发布前由 wrapup.py 脱敏并生成 manifest.sha256；远端 HEAD 以推送后
`git ls-remote` 输出为准（见英文版报告末尾的核验记录）。*
