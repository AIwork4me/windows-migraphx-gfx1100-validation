# R1 根因报告 — Windows gfx1100 HIP 101 invalid device ordinal（中文）

- run_id: `r1_20261116T232607Z`（2026-11-16T23:26Z – 2026-11-17 UTC）
- 分支: `investigation/r1-hip101-root-cause`（base = main `c597e4f3a4bd4a98dbcca9ea36d0286ccb8bc65f`，与远端一致）
- 证据目录: `reports/r1/r1_20261116T232607Z/`（原始 JSON receipt 均含 schema_version/run_id/UTC/退出码）
- 机器: Windows 11 build 26300，AMD Radeon RX 7900 XTX（gfx1100，PCI VEN_1002&DEV_744C），驱动 32.0.21030.2001（2025-09-25）

## 判定：ROOT_CAUSE_ISOLATED

**核心问题**：为什么 WinML 已能发现并注册 AMDGPU/MIGraphX EP，但 `sess.run()` 在
`hip/allocator.cc:14 hipSetDevice(device_id)` 报 `HIP failure 101: invalid device ordinal; GPU=0`？

**根因（已由三层独立真机证据交叉证实）**：

> `amdgpu-ep`（WinML AMD EP 包 1.8.65.0）在 `GetSupportedDevices` 中把 WinML 硬件设备
> 元数据里的 **`DxgiAdapterNumber` 直接当作 HIP 设备 ordinal** 传给
> `hipSetDevice`（见 deps 源码 `src/amdgpu/gpu_factory.cc`，`"Use DxgiAdapterNumber from
> device metadata as the device_id"` 一段）。本机有 4 个显示适配器（GameViewer 虚拟显示器 /
> RX 7900 XTX / Intel UHD 770 / RDP 虚拟显示器），AMD EP 对同一张 7900 XTX 暴露了**两个**
> 设备候选：`DxgiAdapterNumber=1`（LUID 118045，无 Discrete 标志）和 `DxgiAdapterNumber=0`
> （LUID 72346，Discrete=1）。选前者 → `hipSetDevice(1)`；而本机 HIP（EP 自带
> amdhip64_7.dll 与 System32 amdhip64_6.dll 均同）`hipGetDeviceCount()=1`，唯一合法 ordinal
> 是 0 → 返回 **101 invalid device ordinal**。选后者 → `hipSetDevice(0)` 成功 →
> **GPU 真实执行 + parity PASS**。

第一轮代码 `runner/ep_runner.py::add_ep_for_devices` 恒取 `devs[0]`（Dxgi=1 的那个），
所以每次都 HIP 101。**驱动版本不是近因**。

## 证据链（三层）

### 1) R1.1 独立 HIP Runtime 探针（脱离 ORT/WinML，纯 ctypes）

`reports/r1/r1_20261116T232607Z/hip_probe_M*.json`（4 格矩阵全部 `HIP_SET_OK`）：

| DLL | SHA256 | 签名 | runtime/driver version | count | hipSetDevice(0) | 负对照(1/-1/999) |
|---|---|---|---|---|---|---|
| EP 包 `amdhip64_7.dll`（实际加载路径已用 GetModuleFileNameW 证实） | `714990f5b64a767c467882fa5d8165da3cb6861942a834a602803387e533d6cc` | AMD, Valid | 71460850 / 71460850 | 1 | **成功**，`hipDeviceGetName`="AMD Radeon RX 7900 XTX"，24GiB | 101 invalid device ordinal |
| System32 `amdhip64_6.dll` | `8fdb9c0d0b32b96454d092845311912e64b28594ea68f27e8511e1bb16db5b6c` | Microsoft WHCP, Valid | 60450101 / 60450101 | 1 | 成功 | 101 |

清除可见性环境变量（本就未设置）与先 `hipInit(0)` 两格结果相同。负对照复现的正是 EP 报的
101 错误码与文案。`hipGetDeviceProperties` 大结构调用按任务书 ABI 门禁记
`SKIPPED_ABI_UNVERIFIED`（本机无与该 DLL 版本完全匹配的头文件），用
`hipDeviceGetName`/`hipDeviceTotalMem` 无结构接口替代。

### 2) R1.2 EP 设备逐候选真机验证（每候选独立进程）

`epdev_{AMDGPU,MIGraphX}_{enumerate,run_c0,run_c1,multi}.json`：

| EP | 候选 | 身份（metadata） | 结果 |
|---|---|---|---|
| AMDGPU | c0 | DxgiAdapterNumber=1, LUID 118045 | **HIP101**（session 建成、EP 在 get_providers，首个 run 在 allocator 处 101） |
| AMDGPU | c1 | DxgiAdapterNumber=0, Discrete=1, LUID 72346 | **PASS_AMD_GPU_EXECUTED_AND_PARITY**：profile 节点 provider=AMDGPUExecutionProvider，parity=0.0 |
| MIGraphX | c0 | 同上 Dxgi=1 | **HIP101** |
| MIGraphX | c1 | 同上 Dxgi=0 | **PASS_AMD_GPU_EXECUTED_AND_PARITY**：provider=MIGraphXExecutionProvider，parity=0.0 |
| 两者 | 双设备 | — | EP 工厂报 `INVALID_ARGUMENT: only supports selection for a single device`，ORT **自动回退 CPU-only session**（"EP Error / Falling back" 块打在 **stdout**，见下） |

进程模块审计（`dll_load_audit.json`）证实 EP 子进程加载的 `amdhip64_7.dll` 与 R1.1 清单中
同路径同文件（含 migraphx*.dll / hip-backend.dll 全栈，均来自 MSIX 目录）。

### 3) 源码对照（deps/onnxruntime-ep-amdgpu @ 77bba05，只读）

`src/amdgpu/gpu_factory.cc` `GetSupportedDevices`：
`int device_index = i; … if (strcmp(keys[idx], "DxgiAdapterNumber")==0) device_index = atoi(values[idx]);`
随后 `CreateMemoryInfo_V2(..., device_index, ...)`；allocator 侧 `src/shared/hip/allocator.cc`
`SetDevice(device_id)` → `hipSetDevice(device_id)`。报错行号/路径
（`C:\j\amdgpu-ep\src\shared\hip\allocator.cc:14`）与该源一致（构建版行号略移位，函数体相同）。
DxgiAdapterNumber 只有在**恰好等于** HIP ordinal 时才碰巧正确——单卡且为 DXGI 0 号适配器的
机器上无法暴露此缺陷。

## 被排除的假设

| 假设 | 结论 | 证据 |
|---|---|---|
| 驱动过旧（<25.10.13.09）导致 HIP 不能用 | **排除（作为近因）**：同一驱动上裸 HIP 全通，且选对设备候选后 EP 真机 GPU PASS | R1.1 四格 + R1.2 c1 |
| HIP runtime 与驱动 ABI 不匹配 | 排除：hipRuntimeGetVersion/hipDriverGetVersion 均成功返回，设备枚举/属性/内存查询正常 | R1.1 |
| EP 未注册 / 注册失败 | 排除：注册成功且 EP 出现在 get_providers | R1.2/R1.3 B、C |
| 设备可见性环境变量干扰 | 排除：HIP_*/ROCR_*/CUDA_*/HSA_* 全部未设置（receipt 快照） | env_fingerprint.json |
| System32 HIP6 与 EP HIP7 冲突混载 | 排除：EP 进程模块列表只有 MSIX 目录的 amdhip64_7.dll | dll_load_audit.json |

## 仍不确定的点（如实标注）

1. **LUID 118045（DxgiAdapterNumber=1）适配器的物理本体**：它与真卡同 PCI ID（0x744C）、
   同显存 24560MB，但无 `Discrete` 标志、LUID 不同。最可能是 GameViewer 虚拟显示驱动
   （WMI 中存在，ROOT\DISPLAY）包装 7900 XTX 暴露的间接适配器；未做禁用该驱动的对照实验
   （需用户授权），故记 `COMPATIBILITY_UNVERIFIED`，不影响根因结论（无论它是谁，把
   DxgiAdapterNumber 当 HIP ordinal 都是错的）。
2. `32.0.21030.2001` 与 Adrenalin 版本号的权威映射未取得 → `COMPATIBILITY_UNVERIFIED`
   （见 env_fingerprint.json）。升到 25.10.13.09+ 是否改变 DXGI 枚举顺序未测。
3. MIGraphX EP 在 classic `providers=[...]` 路径即使已注册也被丢弃的内部机制
   （本轮只固化了现象，见 ISSUE_151 文档）。

## 对第一轮结论的修订（详见 ERRATA.md）

- AMDGPU/MIGraphX EP 从“本机驱动下 CONDITIONAL NO-GO”修订为
  **“选对设备候选（DxgiAdapterNumber=0）即真机 GPU 可用（GO，附条件：必须显式选对候选）"**，
  前提是使用 device API + `pick_device_by_dxgi_adapter`（见 runner/r1/fail_closed.py）。
  注意：`DxgiAdapterNumber=0` 是**本主机当前枚举下的实验事实**，不是可移植默认值；
  R1 数值对照对象是简单 Add 图 vs NumPy 参考（R2 起真实模型一律用同构建 ORT CPU
  reference 做逐输出比对）。
- "驱动门控”降级为未确证假设；"#151 零告警”限定为旧证据采集缺陷（DEVNULL），
  新证据显示多数回退路径有 stdout/stderr 痕迹。MIGraphX classic 丢弃（B/F3）无
  fallback 专属告警，但其 stderr 仍有一行与回退无关的进程级
  `Init provider bridge failed` 环境初始化告警（所有 cell 均出现，
  见 ISSUE_151 文档注 3a）。

## 下一步最有价值的单一授权实验

**禁用/卸载 GameViewer 虚拟显示驱动后重启，重跑
`runner/r1/ep_device_supervisor.py`**（只读、可回滚：设备管理器禁用即可）。
预期：AMD 只剩一个 DxgiAdapterNumber=0 候选 → `devs[0]` 默认选择即命中 → HIP 101 消失。
该实验能把“虚拟适配器触发 ordinal 错位”从强相关推进为因果确认，并可写进上游
issue 复现条件（多适配器 + AMD 不在 DXGI 0 号位）。风险：显示输出路径变化（若某些
显示器接在该虚拟适配器上）；回滚：重新启用驱动。

次优（并列）：驱动 A/B（升 Adrenalin 25.10.13.09+）— 但按 R1 证据它对本根因无解，
仅可能改变 DXGI 枚举顺序从而“碰巧”绕过；不建议作为首选。
