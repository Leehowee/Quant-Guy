param(
    [string]$TaskName = "IndustryFlowMonitorDaily",
    [string]$PythonExe
)

$ErrorActionPreference = "Stop"
$BinDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $BinDirectory
$EntryPoint = Join-Path $ProjectRoot "bin\run_daily.py"

if (-not (Test-Path -LiteralPath $EntryPoint)) {
    throw "Daily entry point not found: $EntryPoint"
}

if (-not $PythonExe -and (Get-Command python.exe -ErrorAction SilentlyContinue)) {
    $PythonExe = (& python.exe -c "import sys; print(sys.executable)" 2>$null | Select-Object -Last 1).Trim()
}
if (-not $PythonExe -and (Get-Command py.exe -ErrorAction SilentlyContinue)) {
    $PythonExe = (& py.exe -3 -c "import sys; print(sys.executable)" 2>$null | Select-Object -Last 1).Trim()
}
if (-not $PythonExe -or -not (Test-Path -LiteralPath $PythonExe)) {
    throw "Could not resolve Python. Install project requirements and ensure 'python' or 'py -3' works in this PowerShell session."
}

$Runner = Join-Path $ProjectRoot "bin\run_daily_scheduled.ps1"
$PowerShellExe = (Get-Command powershell.exe).Source
$Action = New-ScheduledTaskAction `
    -Execute $PowerShellExe `
    -Argument ('-NoProfile -ExecutionPolicy Bypass -File "{0}" -PythonExe "{1}"' -f $Runner, $PythonExe) `
    -WorkingDirectory $ProjectRoot
$Trigger = New-ScheduledTaskTrigger `
    -Weekly `
    -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday `
    -At "6:00PM"
$Principal = New-ScheduledTaskPrincipal `
    -UserId ("{0}\{1}" -f $env:USERDOMAIN, $env:USERNAME) `
    -LogonType Interactive `
    -RunLevel Limited
$Settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Principal $Principal `
    -Settings $Settings `
    -Description "Fetch THS industry fund-flow snapshots for instant, 3/5/10/20-day periods and send one DingTalk digest on CN trading days." `
    -Force | Out-Null

Write-Output "Registered '$TaskName' for weekdays at 18:00. The script checks the CN trading calendar before fetching."
Write-Output "Python: $PythonExe"
Write-Output "Project: $ProjectRoot"
