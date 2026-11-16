# 安全与脱敏

- 公开证据全部经 `runner/phases/wrapup.py` 扫描：GitHub token 形态
  (`gh[pousr]_...`)、用户主目录（`C:\Users\<name>`）、RDP 会话 ID 均被替换；
  每次运行输出 `reports/redaction_log.json` 列出被改写文件。
- 不入库（.gitignore 强制）：venv、模型权重（*.onnx 除 fixtures）、
  crash dump、安装包、代理配置。fixtures/*.onnx 为本仓库原创合成图。
- 原始 profile JSON（chrome trace）可能含绝对路径与主机名——wrapup 同样
  纳入扫描；`reports/m0/profiles/` 仅保留 case 级摘要后的必要文件。
- 不上传：Windows ML EP DLL、HIP SDK、任何 AMD/微软再分发二进制。
- 凭证：gh 登录凭据留在本机 keyring；任何 receipt/日志在写入时即不得
  包含 token（脚本不读取凭据）。
- 本仓库不包含个人数据；机器名以 `platform.node()` 出现于 receipt 的
  `host` 字段——wrapup 已将其替换为 `WIN-GFX1100-VAL`（随机化代号）。
