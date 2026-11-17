# R2.1 最终报告（中文）— 证据冻结、Guard 实证与 Soak/SSD 根因

Run: `r21_20261117T040739Z`｜主机：原生 Windows 11 26300 + Radeon RX 7900 XTX
(gfx1100)，驱动 32.0.21030.2001｜ORT onnxruntime-windowsml 1.30.1（插件 EP 构建）｜
AMD EP 包 Microsoft.WinML.AMD.GPU.EP.Framework.2 1.8.65.0（未改动）｜
源 R2 run `reports/r2/r2_20261117T004504Z`（冻结未改）。
发布时 HEAD 以 PR #2 描述钉住（`git ls-remote` / gh api 核验，避免自引用哈希提交）。

## A. Executive verdict

| 阶段 | 结论 | 状态 |
|---|---|---|
| 证据冻结 | 464/466 历史 M 文件恢复为 merge-base 原始字节（blob 级 0 偏差）；PR diff 1821→1362 文件 | ✅ GO_EVIDENCE_FREEZE |
| 统计口径 | 290=234+54+2 唯一口径确立；每 EP 117=111 合成+6 真实；manifest 1319 对账 0 坏哈希 | ✅ |
| CI 假阳性 | CPU-only 收据骗不过新门禁（19 个对抗用例红队验证）；真实 234 张 GPU 收据仍全过 | ✅ GO_CPU_VALIDATOR |
| C++ guard | INT_MAX 断言 bug 修复；g++/clang++/MSVC 三编译器 -Werror 实编译实运行全 PASS；原生 EP 构建 BLOCKED_TOOLCHAIN（未变） | ✅ GO_GUARD_HELPER（helper 级） |
| MIGraphX soak | 6 例受控实验（含 R2 精确输入路径复刻）全部干净退出 → **RCA_NOT_REPRODUCED**；R2 历史 FAIL 维持原判 | ✅（NOT_REPRODUCED 是诚实结论） |
| SSD 全 CPU | 机制定位：**EP 接受编译→运行时 kernel 调用 EP_FAIL→会话级 CPU 回退**；最小 4 节点 Loop fixture 同构复现 → `LOOP_BARRIER_ISOLATED`；kernel 内部原因未隔离（闭源 MSIX） | ✅ GO_SSD_RCA（证据级） |
| 发布就绪 | **READY_FOR_OWNER_REVIEW**（非生产就绪、非上游就绪） | — |

## B. 双仓库状态（发布时核验）

- 主仓库 https://github.com/AIwork4me/windows-migraphx-gfx1100-validation
  Draft PR #2（base `main`@7156b9b，R1 合并），分支
  `mission/r2-device-identity-real-models`，仅前向新增提交，无 force-push、未合并。
  R2.1 提交：`0331c55`(A1) → `b56d890`(A3/A4) → `2fb962c`(A5/C1) → `e2d3698`(B/D 工具)
  → `61ace45`(A4 修) → `ff178e2`(workflow 修) → 最终证据提交（本文件所在）。
  CI `r2-cpu-tests` + `r1-cpu-tests` 在 ff178e2 及最终 HEAD 上核验为绿。
- Fork https://github.com/AIwork4me/onnxruntime-ep-amdgpu Draft PR #1
  （指向 fork 自己的 main@15ded64），分支 `fix/windows-dxgi-hip-device-identity`
  `e3dd9af → 0baccb2`（guard 测试修复 + guard-helper-cpp workflow）。CI run
  44016624979：linux-gcc / linux-clang / windows-msvc 三 job 全绿。未向
  onnxruntime/* 上游发任何内容。

## C. 证据冻结明细

- 审计：466 个 M 文件逐一 blob 字节比对 → 464 纯 EOL 差异 + 2 有意修改
  （.gitignore、README.md）；464 全部 `git restore --source=7156b9b` 恢复并验证
  index blob == merge-base blob（0 mismatch）。抽查 `reports/m0/
  m0_AMDGPUExecutionProvider_run.json` blob = `eb8a084b…` 与 R1 原始一致。
- R1 历史 manifest 的 4 处已知不一致：保留原样，仅 errata 注记（未修复、未篡改）。
- 脱敏：全 run 目录扫描 0 命中（`redaction_scan.json`），manifest 排除
  manifest/finalize/redaction 三个后置元数据文件（与 R2 契约一致），自校验 0 错。

## D. R2 矩阵口径修正

290 = **234 PASS_GPU_CORRECT_FULL_COVERAGE + 54 SKIPPED_FIXTURE_DEFECT +
2 FAIL_NO_GPU_COMPUTE**；合成 276（222 PASS + 54 缺陷）、真实 14（12 PASS + 2
SSD FAIL）；每 EP：117 总 PASS = **111 合成 + 6 真实**、27 缺陷、1 SSD FAIL
（此前"每 EP 各 117 合成 PASS"口径混淆，已在 8 项 errata 中更正）。
覆盖语义：`PASS_GPU_EXECUTED`（EP fused 节点 >0）≠ 每个源 ONNX 节点被逐一证明。

## E. Guard helper（fork）

- 修复 `TestInvalidValues` 确定性错误：`Evaluate({"A","2147483647"}, hip=100)`
  必为 `kSkipOutOfRange`（草案误写 kAccept）；INT_MAX 解析经 `ParseAdapterNumber`
  独立证明；新增 range 边界、同 LUID 不同 DXGI 序号、`TestGuardLimits`
  （bounds guard 无法证明物理身份/两 LUID 别名同一 ordinal/合法区间指错卡）。
- 真实编译实证（CI run 44016624979，全部 "all checks passed"）：
  Ubuntu `g++ -std=c++11 -Wall -Wextra -Werror`；Ubuntu `clang++ 21.1.8` 同参；
  Windows Server MSVC `cl /W4 /WX /EHsc /std:c++14`。
- 边界：`GUARD_HELPER_COMPILED_TESTED` ≠ 原生 EP 构建（仍
  PATCH_NATIVE_BUILD_BLOCKED_TOOLCHAIN）≠ 上金属验证（NO_ON_METAL）。`EvaluateAll`
  未接入 factory，测试仅针对 helper。

## F. Soak RCA（yolo26n × MIGraphX 失速）

| case | 变量 | 结果 |
|---|---|---|
| 故障注入×4 | 验证看门狗 | 正确区分正常退出/rc7/无进展 stall/recreate 处 stall（4/4） |
| c1 AMDGPU 持久 | 对照 | 18,514 it 干净退出 |
| c2 MIGraphX 持久 | 无 recreate | 39,684 it 干净退出 |
| c3 MIGraphX recre@2000 | R2 节奏 | 24,000 it / 12 次 recreate 干净（R2 失速点 9856 被穿越） |
| c4 recre@4000 | 间隔×2 | 20,273 it / 5 recreate 干净 |
| c5 显式 teardown+解释器退出 | 拆卸假说 | release 0.04s、gc 0.005s、解释器关停完成 |
| c6 R2 精确输入路径 | 反事实 | 25,849 it / 12 recreate / 1615 it/min（≈R2 速率）干净 |

阶段计时：create_session ≈42s、session_release <0.1s；每阶段 enter/leave 均有
fsync JSONL 证据。**判定 `RCA_NOT_REPRODUCED`**：R2 单次失速与瞬时主机/运行时
条件最一致（R2 历史含孤儿进程污染），不支持确定性 recreate/run/teardown 缺陷；
不为单次混淆失速提上游 issue。R2 历史 FAIL_SOAK_NO_CLEAN_EXIT_HEALTHY_IN_WINDOW
维持不变。

## G. SSD 全 CPU 根因

机制（stdout 实证，R2 日志中本就存在、本次首次连接到结论）：EP capability
**接受**全图 → 编译单 fused kernel（`AMDGPU…_0` / `MIGraphX…_0`）→ 运行时调用
EP_FAIL("Failed to call function") → ORT 插件层重建 CPU-only 会话重跑 →
5,587 CPU 事件、0 EP 节点。4 个优化级（DISABLE_ALL/BASIC/EXTENDED/ALL）×双 EP
全部复现。最小化隔离（opset-10，双 EP）：静态 conv 链 GPU PASS、**动态维度
conv 链也 GPU PASS（排除动态形状假说）**、纯 Loop（4 节点）与 Conv+Loop 均
同构 EP_FAIL+回退 → **`LOOP_BARRIER_ISOLATED`**（控制流子图即屏障）；kernel
内部失败原因未隔离（闭源 MSIX、无本地工具链）→
`KERNEL_FAILURE_CAUSE_NOT_ISOLATED`。修复方向（建议未验证）：导出前展开
Loop/静态化 NMS；上游最小问题（未提交）：amdgpu-ep 是否应在 capability 阶段
拒绝控制流子图以避免整图 post-compile 失败。

## H. CI 对抗回归

19 个用例（14 负向 + 3 正向 + 2 矩阵/manifest）全部按预期：伪造 CPU-only、
EP-0 节点、缺输出、奇偶矛盾、坏 LUID、字符串节点计数、NaN parity、重复/
缺失矩阵行、篡改 manifest…… 每一项都让验证器 FAIL；真实 R2 收据不误杀。
CI 两轮真实缺陷（跨盘 temp 路径、YAML 冒号）已修复并保留在证据链中。

## I. 证据与路径

- 本 run：`reports/r2_1/r21_20261117T040739Z/`（BASELINE/A1_BLOB_AUDIT/
  R2_RECOUNT/MANIFEST_RECON/CI_NEGATIVE_TESTS/R21_GUARD_VALIDATION/SOAK_RCA/
  SSD_PARTITION_RCA/R21_ERRATA/OPEN_GAPS_AND_AUTHORIZATION/R21_LEDGER/
  soak_fault/soak_matrix/ssd_partition/ + manifest.sha256）
- 工具：`runner/r2_1/*.py`、`tests/test_r21_validator.py`、
  `.github/workflows/r2-cpu-tests.yml`（新增门禁步骤）
- Fork：`tests/device_identity_guard_test.cc`、
  `.github/workflows/guard-helper-cpp.yml` @ `0baccb2`
- CI：主 44019060449/44019068997（ff178e2）+ 最终 HEAD 复核；fork 44016624979

## J. 遗留与下一步

P0（需 owner 授权/操作）：设备管理器禁用 GameViewer 虚拟显示 + 重启实验（三
镜像→一镜像，HIP 101 场景消除验证）；driver↔Adrenalin 版本映射验证；移除
`r2-deploy-key`（HTTPS 推送已恢复常态，未自动撤销）。
P1：原生工具链（VS Build Tools+HIP SDK）落地后做 fork 补丁 native 构建与上金
属验证；SSD 控制流展开导出的数值等价验证；上游最小问题草拟（等授权）。
最佳下一步：GameViewer 禁用实验——一次操作同时验证设备身份链与 HIP 101
根因模型，性价比最高。
