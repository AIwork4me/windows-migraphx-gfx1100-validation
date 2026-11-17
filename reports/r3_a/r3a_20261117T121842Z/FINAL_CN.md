# R3-A 最终报告（中文）

Run ID: `r3a_20261117T121842Z`（2026-11-17 UTC）· 分支
`mission/r3a-native-ep-validation`（基于 main `6b780a4`，R2.1.1 merge
`7ae279c` 之后）。

## 结论：`GUARD_HELPER_CI_PASS`（唯一成立层级）；`NATIVE_COMPILED` 及以上
全部 **BLOCKED_DEPENDENCY**（高质量阻塞结论，非失败）。

## A0 版本与依赖实证（全部实测）

- fork：head `0baccb2` = 本地 = 远端 = PR #1 head；base `15ded64` =
  upstream/main 当前 HEAD（**零漂移**）；PR #1 Draft MERGEABLE，
  6 项 CI（g++/clang++/MSVC × push/PR）SUCCESS。
- 主机工具链：cl / cmake / ninja / hipcc / clang++ / g++ 全部
  NOT_FOUND；vswhere 不存在（无任何 VS 实例）；无
  `C:\Program Files\AMD\ROCm`；注册表无 HIP SDK 键。
- **WinML MSIX 开发文件探测（合法只读遍历）**：
  `Microsoft.WinML.AMD.GPU.EP.Framework.2 1.8.65.0` 的
  ExecutionProvider 目录 0 个 `.h`、0 个 `.lib` —— 运行时包不构成
  开发 SDK；任务书禁止从中拆文件充当 SDK，遵守。
- 结论：`BLOCKED_MIGRAPHX_DEV_LIB` + `BLOCKED_HIP_SDK` +
  `BLOCKED_TOOLCHAIN`（MSVC）三重阻塞；管理员安装授权
  `NEEDS_OWNER_AUTH` 未获批准，未做任何系统变更。
- GitHub-hosted Windows runner 评估：镜像自带 MSVC，但同样没有
  Windows HIP SDK 与 MIGraphX 开发库（无公开独立分发），且
  `--use_amdgpu` 需要全部依赖齐备——CI 环境同样无法完成 NATIVE 层；
  已有的 guard-helper-cpp workflow 即为 CI 层可做到的最大真实验证。

## A1 Guard 源码审计（source-only，10 项清单全核）

见 `GUARD_ABI_AUDIT.md`。要点：严格解析/边界检查/同 LUID 去重正确；
`TestGuardLimits` 如实编码"边界通过≠同一物理卡"的物理反例；
hipGetDeviceCount 失败 fail-open 已文档化；DML-only 主机无行为变化；
allocator 单槽覆盖为上游遗留（fork 已加 TODO 注释）；无硬编码 0、
无 clamp、无 MATCHED_IDENTITY 表述。**未发现需要修改的源码错误。**
ABI 声明天花板：三编译器 header-only 编译+运行 —— 不冒充链接级证明。

## A2/A3 原生构建与单变量对照

- 构建：未在主机尝试（依赖缺失实测在案）；无原生二进制 →
  `NATIVE_BINARY_SHA256.json` 记录"不存在"。
- Stock vs Patched 对照：**NOT_TESTED**（无 patched 产物即无对照），
  `ON_METAL_MATRIX.csv` 如实标注；stock MSIX 的真机有效性沿用
  R2 + R2.1.1 已冻结证据。

## A4 交付

- 本目录全部工件 + manifest + 脱敏扫描；
- 分支 `mission/r3a-native-ep-validation` → Draft PR（验证仓库自身，
  不动 fork、不提交 onnxruntime/* 上游、不合并）。

## R3-B handoff

**`STOCK_MSIX_VALIDATED`**（WinML AMD EP 1.8.65.0 经 R2/2.1.1 真机
验证）；`PATCHED_NATIVE_VALIDATED` 不成立（未构建）；即
`PATCHED_NATIVE_BLOCKED`。R3-B 只使用 stock MSIX 1.8.65.0，
GUARD_ONLY 产物不得当作 WinML 官方分发。
