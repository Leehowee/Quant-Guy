# A股行业与 ETF 资金流统计 Skill

一个可安装到 Codex 的 Skill，并附带两个可独立运行的 Python 数据流程：A股行业板块资金流统计，以及 ETF 份额变动与估算净申购统计。

项目只提供描述性统计，不生成买入或卖出信号。行业数据来源与 ETF 份额估算口径不同，报告中会分别说明。

## 安装

克隆仓库到 Codex skills 目录，并安装 Python 依赖。Windows 默认目录为：

```powershell
git clone https://github.com/Leehowee/Quant-Guy.git "$env:USERPROFILE\.codex\skills\a-share-fund-flow"
Set-Location "$env:USERPROFILE\.codex\skills\a-share-fund-flow"
py -3 -m pip install -r requirements.txt
```

重启或刷新 Codex 后，可在请求中使用 `$a-share-fund-flow`，例如：

> 用 $a-share-fund-flow 生成今天的行业和 ETF 资金流统计。

也可以不通过 Skill，直接运行下方 Python 命令。

## 两类统计

### 行业资金流

- 每日读取同花顺/AKShare 的即时、3、5、10、20 日行业资金流快照。
- 计算各周期资金净流入排名、排名变化和描述性特征；仅即时序列用于连续净流入天数等日序列特征。
- 可单独回溯东方财富行业历史数据。东方财富历史值和同花顺快照分开存储，不混算。
- 行业快照金额以数据源返回的亿元口径保存。

### ETF 资金流估算

- 读取上交所、深交所公布的 ETF 份额，与上一交易日比较。
- 按 `份额变化 × 收盘价` 估算净申购/赎回金额，结果不是实际现金流。
- 不纳入上交所交易型货币 ETF；按基金名称排除债券类 ETF。

## 手动运行

在仓库目录执行：

```powershell
# 行业资金流每日快照与特征
py -3 run_daily.py

# ETF 份额变化与净申购估算
py -3 run_etf_flow_daily.py

# 行业历史数据回溯（请求量较大）
py -3 run_backfill.py

# 回溯最近 20 个 ETF 交易日
py -3 run_etf_backfill.py --sessions 20
```

指定 ETF 回溯结束日：

```powershell
py -3 run_etf_backfill.py --sessions 20 --end-date 2026-10-07
```

日报命令默认只采集、保存并在终端显示，不会发送钉钉消息。只有用户明确要求发送时才添加 `--send`：

```powershell
py -3 run_daily.py --send
py -3 run_etf_flow_daily.py --send
```

若明确要求重发已发送的行业日报，可用 `--send --force-send`。ETF 命令也支持 `--force-send`。行业与 ETF 回溯会访问上游数据源并写入本地历史文件，运行耗时取决于请求数量和网络情况。

## 配置

Python 3.10 或更新版本。安装依赖：

```powershell
py -3 -m pip install -r requirements.txt
```

钉钉推送可选。复制 `.env.example` 为 `.env`，只在本地填写所需的机器人配置。`.env`、本地行情数据、日志和报告已列入 `.gitignore`。

常用变量：

| 变量 | 用途 |
| --- | --- |
| `DATA_DIR` | 原始及处理后数据目录，默认 `./data` |
| `REPORT_DIR` | 图片报告目录，默认 `./reports` |
| `DIGEST_TOP_N` | 摘要榜单展示数量，默认 `5` |
| `DINGTALK_WEBHOOK` | 可选的钉钉群机器人 Webhook |
| `DINGTALK_CLIENT_ID`、`DINGTALK_CLIENT_SECRET`、`DINGTALK_OPEN_CONVERSATION_ID` | 可选的钉钉应用机器人图片推送配置 |
| `EASTMONEY_DIRECT` | 是否绕过 Python 代理环境变量直连东方财富，默认 `0` |

## 输出

- `data/raw/daily/`：每日原始 CSV 快照。
- `data/processed/`：行业和 ETF Parquet 历史、特征及断点文件。
- `reports/`：行业和 ETF PNG 报告。

所有这些目录均为本机运行数据，不包含在仓库中。

## Windows 定时任务（可选）

在 PowerShell 中运行 `register_daily_task.ps1` 和/或 `register_etf_daily_task.ps1` 可注册工作日 18:00 任务。定时脚本会显式启用 `--send`；若不希望推送，请直接使用 Python 命令，或不要注册定时任务。

## 数据与使用限制

- 接口数据的覆盖范围、分类与口径可能随供应方调整；运行时应检查实际数据日期及日志。
- ETF 的估算净申购将份额变化乘以单一收盘价，可能与实际现金流不同。
- 这些数据与特征是历史和横截面统计，不构成交易建议或预测保证。
- 使用者应自行遵守数据源服务条款及所在地适用规则。

## 许可证

MIT，见 [LICENSE](LICENSE)。
