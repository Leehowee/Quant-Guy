# A股行业与 ETF 资金流统计 Skill

一个可安装到 Codex 的 Skill，并附带两个可独立运行的 Python 数据流程：A股行业板块资金流统计，以及 ETF 份额变动与估算净申购统计。

项目只提供描述性统计，不生成买入或卖出信号。行业数据来源与 ETF 份额估算口径不同，报告中会分别说明。

## 安装

### 安装为 Codex Skill

Windows PowerShell：

```powershell
New-Item -ItemType Directory -Force "$env:USERPROFILE\.codex\skills" | Out-Null
git clone https://github.com/Leehowee/Quant-Guy.git "$env:USERPROFILE\.codex\skills\a-share-fund-flow"
Set-Location "$env:USERPROFILE\.codex\skills\a-share-fund-flow"
```

macOS / Linux：

```sh
mkdir -p "$HOME/.codex/skills"
git clone https://github.com/Leehowee/Quant-Guy.git "$HOME/.codex/skills/a-share-fund-flow"
cd "$HOME/.codex/skills/a-share-fund-flow"
```

### 安装 Python 依赖

项目需要 Python 3.10 或更新版本。建议使用仓库内的虚拟环境。

Windows PowerShell：

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

macOS / Linux：

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

重启或刷新 Codex 后，可在请求中使用 `$a-share-fund-flow`，例如：

> 用 $a-share-fund-flow 生成今天的行业和 ETF 资金流统计，只保存本地结果，不要发送钉钉。

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

## 数据源与可更新配置

当前数据源清单保存在 [`config/data_sources.json`](config/data_sources.json)，Python 启动时会读取它。可在该文件中更新 AKShare API 名称、同花顺周期映射、东方财富地址、交易所份额接口、ETF 报价接口与债券名称过滤词。

| 数据 | 当前来源与接口 |
| --- | --- |
| 行业每日快照 | 同花顺，经 AKShare `stock_fund_flow_industry` 获取即时、3、5、10、20 日排名 |
| 行业历史回溯 | 东方财富行业列表页 `data.eastmoney.com/bkzj/hy.html`，以及 `push2.eastmoney.com` 和 `push2his.eastmoney.com` 行情接口 |
| 交易日历 | AKShare `tool_trade_date_hist_sina` |
| ETF 份额 | 上交所 AKShare `fund_etf_scale_sse`；深交所 AKShare `fund_scale_daily_szse` |
| ETF 当日价格 | 首选东方财富 AKShare `fund_etf_spot_em`；失败时回退新浪 `fund_etf_category_sina` |
| ETF 历史价格 | 新浪 AKShare `fund_etf_hist_sina` |

配置里的 API 名称和 URL 会在程序下次启动时读取。若上游接口的参数或返回列发生变化，还需要同步调整 `src/industry_flow/source.py` 或 `src/etf_flow/` 中对应的数据解析代码。

目录职责：`bin/` 放命令行入口、Windows 定时任务脚本和 POSIX 定时任务入口；`config/` 放环境变量模板和数据源配置；`src/industry_flow/` 放行业统计；`src/etf_flow/` 放 ETF 统计。

## 手动运行

在仓库目录执行：

```sh
# 行业资金流每日快照与特征
python bin/run_daily.py

# ETF 份额变化与净申购估算
python bin/run_etf_flow_daily.py

# 行业历史数据回溯（请求量较大）
python bin/run_backfill.py

# 回溯最近 20 个 ETF 交易日
python bin/run_etf_backfill.py --sessions 20
```

指定 ETF 回溯结束日：

```sh
python bin/run_etf_backfill.py --sessions 20 --end-date 2026-10-07
```

日报命令默认只采集、保存并在终端显示，不会发送钉钉消息。只有用户明确要求发送时才添加 `--send`：

```sh
python bin/run_daily.py --send
python bin/run_etf_flow_daily.py --send
```

若明确要求重发已发送的行业日报，可用 `--send --force-send`。ETF 命令也支持 `--force-send`。行业与 ETF 回溯会访问上游数据源并写入本地历史文件，运行耗时取决于请求数量和网络情况。

## 配置

钉钉推送可选。将 `config/.env.example` 复制为仓库根目录的 `.env`，只在本地填写所需的机器人配置。`.env`、本地行情数据、日志和报告已列入 `.gitignore`。

Windows PowerShell：

```powershell
Copy-Item config/.env.example .env
```

macOS / Linux：

```sh
cp config/.env.example .env
```

生成 PNG 图片报告时需要系统中有中文 TrueType 字体。macOS 通常自带苹方；Linux 可安装 Noto CJK 字体，例如 Debian/Ubuntu 上运行 `sudo apt-get install fonts-noto-cjk`。也可以在 `.env` 中设置 `REPORT_FONT` 和可选的 `REPORT_FONT_BOLD`，指向字体文件。

常用变量：

| 变量 | 用途 |
| --- | --- |
| `DATA_DIR` | 原始及处理后数据目录，默认 `./data` |
| `REPORT_DIR` | 图片报告目录，默认 `./reports` |
| `DIGEST_TOP_N` | 摘要榜单展示数量，默认 `5` |
| `DINGTALK_WEBHOOK` | 可选的钉钉群机器人 Webhook |
| `DINGTALK_CLIENT_ID`、`DINGTALK_CLIENT_SECRET`、`DINGTALK_OPEN_CONVERSATION_ID` | 可选的钉钉应用机器人图片推送配置 |
| `EASTMONEY_DIRECT` | 是否绕过 Python 代理环境变量直连东方财富，默认 `0` |
| `DATA_SOURCE_CONFIG` | 数据源 JSON 路径，默认 `config/data_sources.json` |
| `REPORT_FONT`、`REPORT_FONT_BOLD` | 可选的常规与粗体中文字体文件路径 |

## 输出

- `data/raw/daily/`：每日原始 CSV 快照。
- `data/processed/`：行业和 ETF Parquet 历史、特征及断点文件。
- `reports/`：行业和 ETF PNG 报告。

所有这些目录均为本机运行数据，不包含在仓库中。

## 定时任务（可选）

Windows 可在 PowerShell 中运行 `bin/register_daily_task.ps1` 和/或 `bin/register_etf_daily_task.ps1` 注册工作日 18:00 任务。Linux 可使用 cron，macOS 可使用 launchd；配置示例见 [CROSS_PLATFORM_SCHEDULING.md](CROSS_PLATFORM_SCHEDULING.md)。

计划任务会显式启用 `--send`；若不希望推送，请直接运行 Python 命令或不要注册计划任务。Windows 详细说明见 [WINDOWS_TASK_SCHEDULER.md](WINDOWS_TASK_SCHEDULER.md)。

## 数据与使用限制

- 接口数据的覆盖范围、分类与口径可能随供应方调整；运行时应检查实际数据日期及日志。
- ETF 的估算净申购将份额变化乘以单一收盘价，可能与实际现金流不同。
- 这些数据与特征是历史和横截面统计，不构成交易建议或预测保证。
- 使用者应自行遵守数据源服务条款及所在地适用规则。

## 许可证

MIT，见 [LICENSE](LICENSE)。
