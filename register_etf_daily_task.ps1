param(
    [string]$TaskName = "EtfFlowMonitorDaily",
    [string]$PythonExe
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$EntryPoint = Join-Path $ProjectRoot "run_etf_flow_daily.py"

if (-not (Test-Path -LiteralPath $EntryPoint)) {
    throw "ETF daily entry point not found: $EntryPoint"
}

if (-not $PythonExe -and (Get-Command python.exe -ErrorAction SilentlyContinue)) {
    $DetectedPython = & python.exe -c "import sys; print(sys.executable)" 2>$null | Select-Object -Last 1
    if ($DetectedPython) {
        $PythonExe = $DetectedPython.Trim()
    }
}
if (-not $PythonExe -and (Get-Command py.exe -ErrorAction SilentlyContinue)) {
    $DetectedPython = & py.exe -3 -c "import sys; print(sys.executable)" 2>$null | Select-Object -Last 1
    if ($DetectedPython) {
        $PythonExe = $DetectedPython.Trim()
    }
}
if (-not $PythonExe -or -not (Test-Path -LiteralPath $PythonExe)) {
    throw "Could not resolve Python. Install project requirements and ensure 'python' or 'py -3' works in this PowerShell session."
}

$Runner = Join-Path $ProjectRoot "run_etf_daily_scheduled.ps1"
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
    -Description "Fetch prior-trading-day SSE/SZSE ETF shares at 18:00, save by data date, and send the DingTalk report on trading days." `
    -Force | Out-Null

Write-Output "Registered '$TaskName' for weekdays at 18:00. The program skips exchange holidays."
Write-Output "Python: $PythonExe"
Write-Output "Project: $ProjectRoot"
