---
name: a-share-fund-flow
description: Run and explain A-share industry and ETF fund-flow statistics with the bundled collectors. Use for current snapshots, historical backfills, or comparisons of either dataset; ETF flow is an estimate.
---

# A-share fund-flow statistics

Use this skill to collect and summarize the project's two descriptive datasets:

The bundled commands run on Windows, Linux and macOS with Python 3.10+. Use the user's configured Python environment; see the README for OS-specific environment setup and font requirements for PNG reports.

- **Industry flow:** Tonghuashun industry snapshots for instant, 3-, 5-, 10- and 20-day periods. Historical Eastmoney data is a separate dataset and is not merged with Tonghuashun figures.
- **ETF flow:** Exchange-reported ETF share changes multiplied by a closing price to estimate subscription/redemption flow. This is an estimate, not an observed cash-flow figure.

## Choose and run a workflow

Run commands from this skill's repository directory. Use the user's configured Python environment. If dependencies are missing, provide the setup command from the README instead of silently installing packages.

| User request | Command |
| --- | --- |
| Today's industry snapshot and descriptive features | `python bin/run_daily.py` |
| ETF share-change and estimated-flow snapshot | `python bin/run_etf_flow_daily.py` |
| Both daily datasets | Run both daily commands above |
| Industry historical backfill | `python bin/run_backfill.py` |
| ETF historical backfill | `python bin/run_etf_backfill.py --sessions 20` |

Only start a historical backfill when requested; it makes many upstream requests and can take a while. For ETF backfills, follow the user's requested range and use `--end-date YYYY-MM-DD` when they specify an end date.

Daily commands save raw snapshots and update local Parquet history under `data/`; report images are saved under `reports/`. Confirm the saved data date and summarize the console result. If a source request fails or returns incomplete data, state that clearly and do not present an older snapshot as current.

## External delivery

Daily commands do not send messages by default. Add `--send` only when the user explicitly asks to send the report. Add `--force-send` only when they specifically ask to resend a report that was already delivered. Keep DingTalk credentials in the local `.env`; OpenClaw channel credentials belong in OpenClaw's own account configuration. Never print or include either set of credentials in summaries. When a route is configured, the command can send through DingTalk and/or OpenClaw.

## Explain the numbers

- Industry snapshots are descriptive rankings and net-flow figures from their named source and period. Do not combine Tonghuashun and Eastmoney values or label unvalidated features as predictive.
- ETF estimated flow is `share_change × close`. It can differ from actual cash subscriptions because price, timing, and other effects are simplified. The source universe excludes Shanghai-listed currency ETFs and name-classified bond ETFs.
- Describe results as statistics, not buy/sell signals or personalized investment recommendations. State the data date, provider and relevant coverage or estimate limits when summarizing.

For installation, configuration, data locations and manual commands, see [README.md](README.md).
