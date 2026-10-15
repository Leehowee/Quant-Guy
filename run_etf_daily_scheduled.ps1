param(
    [Parameter(Mandatory = $true)]
    [string]$PythonExe
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$logDirectory = Join-Path $projectRoot 'data\processed'
$pythonScript = Join-Path $projectRoot 'bin\run_etf_flow_daily.py'
$timestamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$logPath = Join-Path $logDirectory "etf_daily_$timestamp.log"

New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
Set-Location -LiteralPath $projectRoot
$sourcePath = Join-Path $projectRoot 'src'
if ($env:PYTHONPATH) {
    $env:PYTHONPATH = "$sourcePath;$env:PYTHONPATH"
} else {
    $env:PYTHONPATH = $sourcePath
}
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
"[$(Get-Date -Format 's')] starting ETF daily collection" | Set-Content -LiteralPath $logPath -Encoding UTF8

$exitCode = 1
try {
    if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) {
        throw "Python executable not found: $PythonExe"
    }
    if (-not (Test-Path -LiteralPath $pythonScript -PathType Leaf)) {
        throw "ETF entry script not found: $pythonScript"
    }

    # Retry the complete collection on transient exchange/network failures.
    # The Python job writes its sent marker after delivery, preventing retries
    # from duplicating an already-sent DingTalk report.
    $maxAttempts = 3
    for ($attempt = 1; $attempt -le $maxAttempts; $attempt++) {
        "[$(Get-Date -Format 's')] attempt $attempt/$maxAttempts" | Add-Content -LiteralPath $logPath -Encoding UTF8
        $stdoutPath = Join-Path $logDirectory "etf_daily_$timestamp`_$attempt.stdout.tmp"
        $stderrPath = Join-Path $logDirectory "etf_daily_$timestamp`_$attempt.stderr.tmp"
        $argumentList = "`"$pythonScript`" --send"
        $process = Start-Process -FilePath $PythonExe -ArgumentList $argumentList `
            -WorkingDirectory $projectRoot -NoNewWindow -Wait -PassThru `
            -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath
        foreach ($streamPath in @($stdoutPath, $stderrPath)) {
            if (Test-Path -LiteralPath $streamPath) {
                $streamText = Get-Content -LiteralPath $streamPath -Raw -Encoding UTF8
                if ($streamText) {
                    Add-Content -LiteralPath $logPath -Value $streamText -Encoding UTF8
                    Write-Output $streamText
                }
                Remove-Item -LiteralPath $streamPath -Force
            }
        }
        $exitCode = $process.ExitCode
        if ($exitCode -eq 0) { break }
        if ($attempt -lt $maxAttempts) {
            $delaySeconds = 5 * $attempt
            "[$(Get-Date -Format 's')] attempt failed with exit code $exitCode; retrying in $delaySeconds seconds" | Add-Content -LiteralPath $logPath -Encoding UTF8
            Start-Sleep -Seconds $delaySeconds
        }
    }
}
catch {
    $_ | Out-String | Add-Content -LiteralPath $logPath -Encoding UTF8
    Write-Output "ERROR: $_"
    $exitCode = 1
}

"[$(Get-Date -Format 's')] finished with exit code $exitCode" | Add-Content -LiteralPath $logPath -Encoding UTF8
exit $exitCode
