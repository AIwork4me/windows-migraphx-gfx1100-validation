# R2.1.1 最终报告（中文）

Run ID: `r2_1_1_20261117T114559Z`（2026-11-17 UTC）
主机：Windows 11 build 26300 · RX 7900 XTX（gfx1100，物理卡 LUID 72346，
fail-closed resolver + canary + `add_provider_for_devices` 逐案例选择）
运行时：ORT-winml 1.30.1 · MSIX `Microsoft.WinML.AMD.GPU.EP.Framework.2`
1.8.65.0 · HIP `amdhip64_6.dll`（System32）。

## 一、结论一览

1. **P0 冻结**：起点核对无漂移——PR #2 head `2e8894e` = 本地 = 远端，
   base `main@7156b9b` 未变；受保护目录 `reports/m0..m5`、`reports/r1`
   diff 仅 119 个新增（R1 证据在 R2 分支入库），**0 修改 0 删除**；
   `a1_blob_audit` 仅 `.gitignore`/`README.md` 内容变更 + 1 个 EOL-only。
   `r21_validate`：OK（234 GPU PASS receipts evidence-validated）。
2. **P1 合法 fixture**：7 个契约合法控制流 fixture（opset 13），onnx.checker
   + shape_inference + **CPUExecutionProvider 实跑** + 独立 NumPy oracle，
   19/19 输入全过（含全零 NonZero、精确抵消计数等边界）。旧
   `static_loop` 的契约违规（carried 形状不一致、scan 伪声明、opset 10
   无 Loop schema）已在 FIXTURE_REGISTRY 中以哈希留档。
3. **P2 真机矩阵（28 案例 + SSD 全图 2 案例）**：
   - `valid_loop_carried` **双 EP × 双优化级 4/4 PASS_GPU_EXECUTED**
     （EP 节点=3，数值 parity 全过）→ **正式撤销 R2.1 "Loop 必然是屏障"**；
   - `valid_loop_scan`：carried 输出精确、**scan 输出数值全错**
     （静默错误结果，无回退）；
   - `valid_nonzero_static/dynamic`：EP 执行但**返回固定计数 [2,20]**
     （全零输入本应 [2,0]）；
   - `valid_nonzero_in_loop`（SSD 同构）：carried 正确、计数 scan
     形状/数值双错（GPU [3,1] vs 期望 [3]）；
   - `valid_conv_then_loop`：EP 内部 parse_loop **b_seq 步长错误**
     （`{64,1,8,1}` vs packed `{64,64,8,1}`）→ EP_FAIL → 会话级回退；
   - SSD 全图新证（`ssd_full/`）：双 EP FAIL_NO_GPU_COMPUTE，MIGraphX
     stderr 复现 `check_dynamic: nonzero: Dynamic shapes not supported`；
     子图遍历证实 **180 个 NonZero 全部在首个 NMS Loop 体内**。
   详见 `SSD_CAUSALITY_TABLE.md`。
4. **P3 勘误**：`R211_ERRATA_SUPPLEMENT.md`（E-R2.1.1-1 撤销 / -2 重新
   归因 / -3 新缺陷类别）；历史 `reports/r2_1` 原始文件未动；README 顶部
   更正 + LATEST 语义澄清（"当时发布的 SHA"）。CI 新增 R2.1.1 fixture
   构建 + CPU oracle + 契约回归步骤（明确 CPU CI 不证明 GPU）。
5. **R2 旧口径不动**：234/290 接受原文；R2.1.1 增量矩阵单列。

## 二、合并门禁评估（P4）

| 条件 | 结果 |
|---|---|
| 受保护历史目录 blob 完全未变 | GO（0 M / 0 D） |
| 新证据 manifest 全部校验 | GO（见 manifest.sha256 自检） |
| 合法 fixture CPU oracle 全过 | GO（19/19） |
| GPU case 判定不虚标 | GO（PASS 仅 4 例 carried-Loop；其余 FAIL/错误如实标注） |
| SSD 因果措辞与回执一致 | GO（errata + 因果表与原始日志一致） |
| R2 旧 234/290 未篡改 | GO |
| PR #2 无不可解释大面积格式变更 | GO（变更均为新增证据/脚本/CI/README） |
| 精确远端 PR HEAD CI 全绿 mergeable | 待本提交推送后核验（执行时记录） |

**决定：MERGED_GO**——在最终 HEAD 的 push+PR CI 双绿、mergeable 状态下
执行 squash 合并 PR #2；合并后以 GitHub API 核验 `merged=true`、merge
commit SHA、main 新 HEAD。实际结果见 `finalize_summary.json`（manifest
后置元数据，by-design 不入 manifest）。

## 三、交付物清单（本目录）

- `BASELINE.json`（PR/CI/GPU/MSIX/模型 SHA256 现场）
- `A1_BLOB_AUDIT.json`（merge-base→head blob 审计）
- `FIXTURE_REGISTRY.json`（7 fixture 哈希 + 旧 fixture 缺陷留档）
- `CPU_ORACLE.json`（19/19）
- `gpu_matrix/`（28 案例回执 + 原始 stdout/stderr + 矩阵 CSV）
- `ssd_full/`（SSD 全图双 EP 新证据 + stderr）
- `SSD_CAUSALITY_TABLE.md`、`R211_ERRATA_SUPPLEMENT.md`
- `FINAL_CN.md` / `FINAL_EN.md`、`redaction_scan.json`、`manifest.sha256`

## 四、R3-A handoff

- 主仓库 `main` 合并后 SHA：见 `finalize_summary.json`（合并执行后写入）。
- fork PR #1（AIwork4me/onnxruntime-ep-amdgpu，Draft）head `0baccb2`，
  CI 绿（guard-helper-cpp 三编译器）；R2.1.1 未改动 fork。
- 与上游补丁相关的新发现：R2.1.1 的三族 EP 缺陷（scan 数值、NonZero
  固定计数、Conv→Loop 步长）都在 EP 编译器/算子层，与 fork 的 DXGI 越界
  guard 属不同层——guard 补丁不受影响，但 FULL_FIX_GAP 需并列列出。

## 五、复现命令

```
git checkout <merged_main_sha>
python runner/r2_1_1/build_valid_controlflow_fixtures.py <run_id>
python runner/r2_1_1/cpu_oracle_r211.py <run_id>
python runner/r2_1_1/gpu_matrix_driver.py <run_id>       # 需真机 gfx1100 + MSIX EP
python runner/r2_1_1/regen_matrix_csv.py reports/r2_1_1/<run_id>/gpu_matrix
```
