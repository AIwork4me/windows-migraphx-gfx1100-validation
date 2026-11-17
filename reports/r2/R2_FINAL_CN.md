# R2 最终报告（中文）— Windows AMD EP 设备身份与真实模型验证

> **R2.1 勘误（2026-11-17）**：本报告有 8 处表述已在
> `reports/r2_1/r21_20261117T040739Z/R21_ERRATA.md` 更正（每 EP 117 合成 PASS 口径、
> YOLO 加速比为 mean 比而非 p50 比、1314→1319 manifest 计数、LATEST.md 过期 SHA、
> bus.jpg 实际已入库、MODEL_MATRIX 空投影、soak 结论措辞、CI 门禁强度）。
> 原始 run 目录 `reports/r2/r2_20261117T004504Z/` 未改动。

Run: `r2_20261117T004504Z`｜主机：原生 Windows 11 26300 + Radeon RX 7900 XTX (gfx1100)｜
ORT: onnxruntime-windowsml 1.30.1（插件 EP 构建）｜AMD EP 包：Microsoft.WinML.AMD.GPU.EP.Framework.2 1.8.65.0（官方 MSIX，未改动）
证据根目录：`reports/r2/r2_20261117T004504Z/`（1314 文件，SHA256 manifest 自校验通过；脱敏扫描 0 命中）

## A. Executive verdict

| 模块 | 结论 |
|---|---|
| **R2-A 应用 GPU 就绪** | **GO（本主机）**：解析器选定物理 XTX 后，AMDGPU/MIGraphX 双 EP 真机执行 234/290 矩阵 case 全覆盖 PASS（含 YOLO26n 端到端 5/5 检测一致、SCRFD 9 输出、4 个 Zoo 分类模型）；ssd_mobilenet 双 EP 0 GPU 节点（整图 CPU 分区，可见非静默） |
| **R2-B 设备身份** | **CONDITIONAL_GO_HOST_SPECIFIC**：物理 XTX = LUID 72346（DXCore Discrete=1 + 唯一活跃 DXGI 输出 + HIP ordinal0 名称/显存一致）；本机三镜像成因已从 ORT 源码+实测双证；HIP PCI BDF 为占位值 NOT_EXPOSED；GameViewer↔LUID118045 归属 GAMEVIEWER_CAUSALITY_UNCONFIRMED；可移植性未证明 |
| **R2-C 上游补丁** | **PATCH_PREPARED_UNBUILT（GUARD_ONLY_NOT_FULL_FIX）**：fork Draft PR（guard + 纯 C++ helper + 8 组单测 + 设计文档）；无 MSVC/SDK/HIP SDK，未编译未运行，不冒称 |
| **R2-D 开源交付** | R1 PR #1 已按门禁合并（merge `7156b9b`，CI 绿）；R2 新分支 `mission/r2-device-identity-real-models` + Draft PR（见 §B）；未向 onnxruntime/* 上游发布任何 issue/PR/评论 |

**一句话最重要新发现**：本机 DXGI 层其实有**三个** 7900 XTX 镜像（R1 只知两个）——ORT core 的远程显示去重启发式只删"高性能序最后一个"，第二个镜像（LUID 118045）因排在中间而漏网，被 amdgpu-ep 当作 HIP ordinal 1 使用即 HIP 101；同时 YOLO26n 官方导出模型在双 EP 上以 3.5× 加速、逐检测一致地真机运行。

## B. 远端代码状态

| 项 | 值（推送后核对） |
|---|---|
| 主验证 repo | https://github.com/AIwork4me/windows-migraphx-gfx1100-validation |
| R1 PR #1 | https://github.com/AIwork4me/windows-migraphx-gfx1100-validation/pull/1 —— **已合并**，merge commit `7156b9b`（r1.4 加固后 CI 绿） |
| R2 分支 | `mission/r2-device-identity-real-models`（自合并后 main `7156b9b` 创建） |
| R2 Draft PR | 见 LATEST.md / PR 链接（Draft，供审阅，不自动合并） |
| 上游 fork PR | https://github.com/AIwork4me/onnxruntime-ep-amdgpu/pull/1（Draft，base=fork 自身 main，head `e3dd9afc27ae47bb343240137b7c22f301dcce81`，基于 upstream main `15ded64`） |
| CI | `r1-cpu-tests` + `r2-cpu-tests`（纯 CPU：compileall/unittest/ledger+receipt 模式/manifest 校验；无 GPU，不验证任何 GPU 主张） |

本地=远端=PR HEAD 核验：发布后以 `git ls-remote` + `gh api` 逐 SHA 核对（见 LATEST.md 核验块）。

## C. 设备身份（R2.1）

| DXGI idx | 描述 | LUID | flags | 输出(活跃) | Discrete | 进 ORT ep 列表 | 判定 |
|---|---|---|---|---|---|---|---|
| 0 | RX 7900 XTX | **72346** | 0 | 1（`\\.\DISPLAY1` 活跃） | 1 | 是 | **物理 XTX** |
| 1 | RX 7900 XTX | 118045 | 0 | 0 | 无 | 是（→HIP 101） | 间接显示镜像 |
| 2 | UHD 770 | 80099 | 0 | 0 | 0 | 仅 DML | iGPU |
| 3 | RX 7900 XTX | 617528 | 0 | 0 | 无 | 否（被去重） | 间接显示镜像 |
| 4 | Basic Render Driver | 79992 | 2(SOFTWARE) | 0 | — | 否 | CPU 回退适配器 |

- PnP：XTX = PCI 总线 3/设备 0/功能 0；证据强度：DXGI 枚举自校验 S1–S6 全过（IID/vtable/结构体源自 mingw-w64 公共头，GetDesc 与 GetDesc1 双路互证）。
- **HIP PCI 属性为占位值**（PciBusId=0, PciDeviceId=1, PciDomainId=64/2 随运行时而异）→ NOT_EXPOSED，HIP↔PCI 无法直连；身份靠三重证据（Discrete+活跃输出+canary）。
- GDI 侧：当前会话全部 16 个 `\\.\DISPLAY*` 均为 RdpIdd_IndirectDisplay（RDP 会话活跃），桌面由物理 XTX 承载。
- **GameViewer 因果未确认**：两镜像（118045/617528）与两个间接显示 PnP 适配器（GameViewer ROOT\DISPLAY\0000、MS Remote RDPIDD 会话 2）按创建顺序对应是合理推断，但无公开 API 把无输出镜像 LUID 映射到 PnP 实例 → 维持 GAMEVIEWER_CAUSALITY_UNCONFIRMED；授权实验方案见 docs/EXPERIMENT_REQUIRES_AUTHORIZATION.md（未执行）。
- 独立 canary：物理候选 PASS（全 EP 节点+CPU parity）；镜像候选 HIP 101（R1 复核 + R2 解析器拒绝路径）。
- 应用层 fail-closed 解析器：`runner/r2/device_resolver.py` + 19 单测（镜像/双卡/iGPU/顺序置换/缺元数据/重复 LUID/显式选择/PCI 需 join 等），本 run 所有 GPU case 均经它选定设备。

## D. 实测兼容矩阵（R2.2）

- 合成矩阵：34 fixture × 2 EP × b1/2/4（+多输出 4 fixture × DISABLE_ALL/BASIC/EXTENDED × b1/2/4）= 290 case；**234 PASS_GPU_CORRECT_FULL_COVERAGE**（逐 EP 各 117 合成 PASS）；54 例为 6 个 fixture 自身图缺陷（纯 CPU 会话同样加载失败，记 SKIPPED_FIXTURE_DEFECT，CPU 证据留档）；2 例 ssd FAIL_NO_GPU_COMPUTE。
- **#150 专项**：two_out_same_shape / two_out_diff_shape / nine_out / split2(defect) × 4 优化级 × 3 batch × 2 EP 全部 by-name 逐输出比对；同形换位探针未触发 swap_suspected；真实 9 输出 SCRFD 亦严格 by-name 全对齐。
- 真实模型（全部同构建 ORT CPU 参考、逐输出 by-name、预设容差 fp32 atol1e-2/rtol1e-2，事后未放宽）：

| 模型 | AMDGPU EP | MIGraphX EP | 备注 |
|---|---|---|---|
| mnist-8 / squeezenet1.0-12 / mobilenetv2-12 / emotion-ferplus-8 | PASS 全覆盖 | PASS 全覆盖 | Zoo 官方资产 |
| **YOLO26n（官方导出）** | PASS 全覆盖（张量 max_abs 0.0044；检测 5/5 与 CPU 一致；类别 {person,bus} 符合 bus.jpg） | PASS 全覆盖 | 端到端：letterbox→GPU→NMS→检测 |
| **SCRFD det_10g（9 输出）** | PASS 全覆盖 @640 | PASS 全覆盖 @640 | 首选多输出检测器 |
| ssd_mobilenet_v1_10 | FAIL_NO_GPU_COMPUTE（0/5587 节点） | 同左 | 整图 CPU 分区；DML（第一夜）可跑 → EP 能力差异 |
| OCR PP-OCRv4 det+rec | BLOCKED_TOOLCHAIN | 同左 | 官方资产已下载；paddle2onnx 原生 DLL 无法加载（精确错误留档） |
| SAM/SAM2 | BLOCKED_MODEL_ASSET | 同左 | 无许可清晰的官方 ONNX 可得 |
| X-AnyLabeling 适配（P2） | NOT_TESTED | — | 未启动，本轮不称 PROTOTYPE_GO |

Receipts：`reports/r2/<run>/cases/*.json`（每个含 providers、nodes_by_provider、parity per-output、device_resolution、时间与 SHA256）。

## E. 性能与稳定性（R2.3，仅 GPU correctness PASS 模型）

| 模型×EP | GPU p50/p95 (ms) | CPU p50 (ms) | 加速 | 冷启动 |
|---|---|---|---|---|
| YOLO26n × AMDGPU | 2.733 / 3.344 | 9.812 | **3.46×** | 会话创建 41.8s（EP 编译）+ 首跑 0.03s |
| YOLO26n × MIGraphX | 2.726 / 3.047 | 10.136 | **3.63×** | 同上 41.8s |
| SCRFD det_10g × AMDGPU | 4.043 / 4.548 | 22.420 | **5.47×** | 首跑 24.3s（编译） |
| mobilenetv2-12 × AMDGPU | 0.828 / 1.106 | 1.342 | 1.65× | 首跑 16.2s（编译） |
| squeezenet1.0-12 × AMDGPU | 0.690 / 0.892 | 1.271 | 1.78× | 会话创建 11.3s |
| matmul(64维,合成) × 双EP | ~0.15 | 0.007 | 0.04×（GPU 更慢） | launch 开销主导，如实记录 |

- 线程设定：default（记录于 receipt）；GPU 显存/利用率：NOT_MEASURED（无可靠用户态计数器，不编造）；进程工作集仪表失效 → NOT_MEASURED。
- Soak（yolo26n × 2 EP × 15 分钟预算，变输入 + 会话重建 + 30s 心跳 + 看门狗）：
  **AMDGPU PASS_SOAK_COMPLETED**（15.55 分钟、24000 迭代、0 错误、12 次会话重建、rc=0 干净退出）；
  **MIGraphX FAIL_SOAK_NO_CLEAN_EXIT_HEALTHY_IN_WINDOW**（窗口内心跳至 iter 9856、0 错误、4 次重建，随后 20 分钟无新 checkpoint 且进程存活 → 看门狗树杀；疑似退出路径/长跑停滞，未隔离复现，不作上游结论）。无 TDR/设备重置事件；不据此宣称"无泄漏"。

## F. 上游补丁（R2.4）

- 上游 main SHA：`15ded64`（fork 时点）；已核查上游**无既有修复**（搜索 DxgiAdapterNumber/HIP ordinal/LUID 的 PR/issue，评审 #139–#171）。
- 变更：`gpu_factory.cc` Windows guard（hipGetDeviceCount 一次 + 严格解析 DxgiAdapterNumber + 越界/缺失/非法候选跳过并告警 LUID/原值/原因/建议动作）+ `device_identity_guard.h` 纯函数 + 8 组场景单测（单文件、无构建系统依赖）+ 3 份文档（设计/issue 草稿/PR 草稿）。
- 不变量：DXGI index/LUID/PCI BDF/HIP ordinal 四命名空间不互换；不硬编码 0；不 clamp 到 count-1（会把响亮崩溃换成静默错卡执行）；多设备 `gpu_memory_info_` 覆盖问题只加 TODO 不冒修。
- 实测状态：mock/单测 PATCH_PREPARED_UNBUILT（无编译器未编译）；完整修复选项（`hipDeviceProp_t.luid` 映射——mgx WSL 路径已在用、EP 初始化 canary、per-device allocator map、ORT core 去重修正）与 5 个维护者问题写入设计文档。
- 禁区合规：未向 onnxruntime/* 提交任何内容；fork PR base 为 fork 自身 main。

## G. 证据审计

- 许可/出处：`MODEL_PROVENANCE.csv`（8 资产：URL、许可证、获取日、SHA256；权重 gitignored，仅公开哈希）。
- manifest：`manifest.sha256` 1314 条自校验 0 错；构建顺序 receipts→脱敏扫描（0 命中）→manifest→自检（不 hash manifest 自身）。
- 历史不可变：R1/round-1 原始 receipts 未改动；本 run 失败→修复→重跑的中间 receipt 全部保留在 git 历史（如 log/sqrt 输入域修复、SCRFD 输入尺寸修复）。
- 失败隔离与断点：每 case 独立子进程 + 超时 + ledger PENDING/RUNNING/DONE/FAILED；case 身份键含 model_sha/EP/batch/opt/ORT/设备指纹；NATIVE_CRASH/TIMEOUT 由 supervisor 分类。
- CI：`r2-cpu-tests` 结构化校验（PASS 必须同时具备 providers+节点归属+parity+device_resolution 四要素，否则 CI 失败）。

## H. 剩余 GAP 与下一步

**P0/P1（需用户授权）**
1. 禁用 GameViewer 虚拟显示适配器→重启→重跑身份 supervisor（因果确认实验，方案+回滚已写）。
2. 安装 VS2022 + Windows SDK + HIP SDK（管理员）→ 编译 fork 补丁 + 原生单测 → 才可能升级 PATCH_PREPARED_UNBUILT 并启动上游沟通（发 issue/PR 仍需授权）。
3. OCR：在有 MSVC 运行时的环境完成 paddle2onnx 转换或获取官方 ONNX。
**P2**
4. SAM/SAM2 官方导出链（需 torch 导出）；X-AnyLabeling 外置适配原型（resolver API 已就绪）。
5. 工作集/显存仪表修复后补测 soak 斜率；更长时间（≥30min/多轮）soak。

## I. 最终结论

**READY_FOR_OWNER_REVIEW**（有条件）：应用侧（R2-A/B）在本主机达到 GO——真实检测/多输出/分类模型双 EP 真机可用且可复现；设备身份可解释、可审计、fail-closed。上游修复（R2-C）为已备未建，通用 Windows 兼容性与上游合并不在本轮主张内。所有 GPU PASS 均满足任务书四要素；所有未达项均以原始证据归类，无一处以 CPU 结果冒充 GPU。
