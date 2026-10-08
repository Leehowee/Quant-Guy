param(
    [Parameter(Mandatory = $true)]
    [string]$PythonExe,
    [switch]$ForceSend
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$EntryPoint = Join-Path $ProjectRoot "run_daily.py"
$LogDir = Join-Path $ProjectRoot "data\processed"
New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
$LogFile = Join-Path $LogDir ("daily_{0}.log" -f (Get-Date -Format "yyyyMMdd_HHmmss"))
$StdoutFile = "$LogFile.stdout.tmp"
$StderrFile = "$LogFile.stderr.tmp"
$Utf8 = New-Object System.Text.UTF8Encoding($false)

Set-Location -LiteralPath $ProjectRoot
$env:PYTHONIOENCODING = "utf-8"
"[$(Get-Date -Format s)] starting daily collection" | Set-Content -LiteralPath $LogFile -Encoding UTF8
$PythonArgs = @('"{0}"' -f $EntryPoint)
$PythonArgs += "--send"
if ($ForceSend) {
    $PythonArgs += "--force-send"
}
try {
    $Process = Start-Process `
        -FilePath $PythonExe `
        -ArgumentList $PythonArgs `
        -WorkingDirectory $ProjectRoot `
        -NoNewWindow `
        -Wait `
        -PassThru `
        -RedirectStandardOutput $StdoutFile `
        -RedirectStandardError $StderrFile
    if (Test-Path -LiteralPath $StdoutFile) {
        [System.IO.File]::AppendAllText($LogFile, [System.IO.File]::ReadAllText($StdoutFile, [System.Text.Encoding]::UTF8), $Utf8)
    }
    if (Test-Path -LiteralPath $StderrFile) {
        [System.IO.File]::AppendAllText($LogFile, [System.IO.File]::ReadAllText($StderrFile, [System.Text.Encoding]::UTF8), $Utf8)
    }
    $ExitCode = $Process.ExitCode
} catch {
    [System.IO.File]::AppendAllText($LogFile, "[$(Get-Date -Format s)] runner error: $($_.Exception.Message)`n", $Utf8)
    $ExitCode = 1
} finally {
    Remove-Item -LiteralPath $StdoutFile, $StderrFile -Force -ErrorAction SilentlyContinue
}
[System.IO.File]::AppendAllText($LogFile, "`n[$(Get-Date -Format s)] finished with exit code $ExitCode`n", $Utf8)
exit $ExitCode
