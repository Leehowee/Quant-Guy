# Windows 每日任务

每日 18:00 启动（周一至周五）。程序先检查 A 股交易日；非交易日跳过。交易日按顺序抓取同花顺即时、3、5、10、20 日行业资金流，保存原始 CSV 和分周期 Parquet，并合并成一条钉钉消息。

## 注册任务

先在项目 `.env` 填写 `DINGTALK_WEBHOOK`，并确保运行任务的 Python 已安装 `requirements.txt` 中的依赖。然后在项目目录 PowerShell 执行：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\register_daily_task.ps1
```

注册脚本会解析当前 `python.exe` 或 `py -3` 对应的绝对解释器路径。若 Python 不在 PATH 中，请通过 `-PythonExe` 显式传入解释器完整路径。任务使用当前用户交互会话，因此用户注销时不会后台运行。

## 手动启动和检查

```powershell
Start-ScheduledTask -TaskName IndustryFlowMonitorDaily
Get-ScheduledTask -TaskName IndustryFlowMonitorDaily | Get-ScheduledTaskInfo
```

每次运行单独写日志，查看最近日志：

```powershell
$log = Get-ChildItem 'data\processed\daily_*.log' | Sort-Object LastWriteTime -Descending | Select-Object -First 1
Get-Content $log.FullName -Tail 80 -Wait
```

如果任务错过计划时间，会尽快补启动。钉钉机器人 Webhook 未填写时，数据照常采集，但不会发送通知。

## 历史回溯

`run_backfill.py` 不属于每日任务。需要时手动执行；每日快照使用独立的同花顺历史库，不依赖回溯完成状态。

## ETF 份额监控任务

ETF 任务每周一至周五 18:00 启动；交易日读取当天沪深 ETF 份额，校验两所日期一致后，与上一交易日份额比较。收盘后读取当天最新报价作为收盘价，估算净申购额；若在 15:05 前手动运行，则跳过实时价格。上交所交易型货币 ETF 不纳入。日文件按实际数据日期命名，例如 `etf_flow_20261008.csv`；非交易日自动跳过。首次没有上一交易日快照时只建基准，不推送。

若要将日报 PNG 发到钉钉群，请在本机 `.env` 配置应用机器人的 `DINGTALK_CLIENT_ID`、`DINGTALK_CLIENT_SECRET` 和 `DINGTALK_OPEN_CONVERSATION_ID`；若 `robotCode` 不同于 Client ID，再填写 `DINGTALK_ROBOT_CODE`。程序会按实际数据日期保存 `reports/etf_flow_YYYYMMDD.png` 并上传图片。应用机器人接口拒绝目标群会话时，若同时配置了群 Webhook，程序会改由 Webhook 发送图片 Markdown。仅配置 `DINGTALK_WEBHOOK` 时会沿用 Markdown 文本推送。Client Secret 和 Webhook 地址不要提交到代码仓库或发送到聊天中。

注册或更新 ETF 任务：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\register_etf_daily_task.ps1
```

任务名为 `EtfFlowMonitorDaily`。行情优先使用东方财富；不可用时使用新浪 ETF 报价。两者都不可用时仍保存份额数据，估算资金流留空。ETF 运行日志写入 `data\processed\etf_daily_*.log`。
