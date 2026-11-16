# 局限与缺口（诚实清单）

1. **AMD EP GPU 真执行未达成（本机驱动门槛）**：EP 安装/注册/工厂创建全部
   走通，但 `hipSetDevice` 即败（HIP 101）。因此本夜**没有任何一条 AMD GPU
   PASS**；所有 AMD EP case = FAIL(HIP 101) 或 BLOCKED_DEPENDENCY（EP 未安装
   阶段）。不存在假 GPU 结论。
2. **驱动未升级**：任务规则禁止更新显卡驱动。官方 MIGraphX EP 要求
   25.10.13.09+；升级驱动+复测是首要后续动作（需用户授权）。
3. **DML 行仅为对照**：`PASS_CONTROL_DML` 仅证明「测试方法学在真 GPU 上能
   验证 placement+parity」，不表示 MIGraphX/AMDGPU 可用。
4. **原生 ROCm 10.1 构建未执行**：无 MSVC/VS2022/Windows SDK/HIP SDK，且
   Build Tools/HIP SDK 安装均需管理员权限（未授权）→ M4 = BLOCKED_DEPENDENCY，
   已钉住上游 SHA 与具体缺失清单，未编译任何东西，未伪造 DLL。
5. **性能数字范围**：仅 CPU vs DML 对照 + session 创建/首轮编译时间；
   AMD EP 无法出性能数。样本数与分位数在 `reports/performance.csv`/
   m3 receipts 中如实施报。
6. **Windows ML WinRT Python 投影不可用**（REGDB_E_CLASSNOTREG）；改用
   NuGet 2.5.96 的 C API（ctypes）——该路径官方支持但文档以 C/C++ 为主，
   Python 侧为本仓库自行桥接。
7. **网络重定向**：nuget.org API 302 到 nuget.azure.cn；github.com 直连
   超时，git 推送经本机 Clash 代理（127.0.0.1:7897）。EP MSIX 的首次下载
   经历 WU staging `0x80073CF8` 失败两次后，在第三次尝试（MIGraphX ensure）
   成功安装——WU 通道对网络敏感。
8. **模型覆盖**：M2 仅官方 Model Zoo 小模型（分类/检测多输出）；YOLO26/
   SAM2/OCR 权重因许可与体积未纳入；X-AnyLabeling 集成仅到文档级
   adapter 方案（docs/X_ANYLABELING_INTEGRATION_GAP.md）。
9. **时间盒**：稳定性 soak 与性能重复轮次以截止时间为准（ledger 有
   NOT_RUN_TIME_BUDGET 标记机制）。
