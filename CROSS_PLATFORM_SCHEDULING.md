# Linux 与 macOS 定时运行

日报入口默认只采集并保存结果。下面的计划任务示例显式传入 `--send`，会尝试发送钉钉；请先在项目根目录 `.env` 配置推送凭据。程序会自行跳过非 A 股交易日。

计划任务会使用项目 `.venv` 中的 Python；如果未创建该虚拟环境，可通过环境变量 `PYTHON_BIN` 指定 Python 可执行文件。

## Linux：cron

先创建日志目录：

```sh
mkdir -p data/processed
```

用 `crontab -e` 添加需要的任务。把示例路径替换为仓库的绝对路径；cron 使用系统本地时区。

```cron
0 18 * * 1-5 /bin/bash "/absolute/path/Quant-Guy/bin/run_scheduled.sh" industry
0 18 * * 1-5 /bin/bash "/absolute/path/Quant-Guy/bin/run_scheduled.sh" etf
```

每次执行的标准输出和错误都会写入对应的 `data/processed/industry_scheduled_*.log` 或 `etf_scheduled_*.log`。

## macOS：launchd

创建 `~/Library/LaunchAgents/com.leehowee.quant-guy.industry-flow.plist`。将示例中的仓库路径替换为绝对路径；`launchd` 不会展开 `~` 或环境变量。先运行一次 `mkdir -p data/processed`，确保日志目录存在。

下面的 `Weekday` 使用 Apple 日历编号：1 表示星期日，因此 2 到 6 表示周一至周五。参见 [Apple 的定时任务说明](https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/ScheduledJobs.html)。

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.leehowee.quant-guy.industry-flow</string>
    <key>WorkingDirectory</key>
    <string>/absolute/path/Quant-Guy</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>/absolute/path/Quant-Guy/bin/run_scheduled.sh</string>
        <string>industry</string>
    </array>
    <key>StartCalendarInterval</key>
    <array>
        <dict><key>Weekday</key><integer>2</integer><key>Hour</key><integer>18</integer><key>Minute</key><integer>0</integer></dict>
        <dict><key>Weekday</key><integer>3</integer><key>Hour</key><integer>18</integer><key>Minute</key><integer>0</integer></dict>
        <dict><key>Weekday</key><integer>4</integer><key>Hour</key><integer>18</integer><key>Minute</key><integer>0</integer></dict>
        <dict><key>Weekday</key><integer>5</integer><key>Hour</key><integer>18</integer><key>Minute</key><integer>0</integer></dict>
        <dict><key>Weekday</key><integer>6</integer><key>Hour</key><integer>18</integer><key>Minute</key><integer>0</integer></dict>
    </array>
    <key>StandardOutPath</key>
    <string>/absolute/path/Quant-Guy/data/processed/launchd_industry.log</string>
    <key>StandardErrorPath</key>
    <string>/absolute/path/Quant-Guy/data/processed/launchd_industry.log</string>
</dict>
</plist>
```

加载并检查任务：

```sh
launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.leehowee.quant-guy.industry-flow.plist"
launchctl print "gui/$(id -u)/com.leehowee.quant-guy.industry-flow"
```

卸载任务：

```sh
launchctl bootout "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.leehowee.quant-guy.industry-flow.plist"
```

要安排 ETF 日报，可复制 plist 并修改 `Label`、`ProgramArguments` 中最后的 `industry` 为 `etf`，以及输出日志文件名；加载时使用新的 plist 路径。
