param(
    [string]$ProjectRoot = (Get-Location).Path,
    [string]$InstallRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..')),
    [switch]$NoAttach,
    [switch]$NoPause
)

$ErrorActionPreference = 'Stop'
try {
    Set-Location -LiteralPath $ProjectRoot
    $launcher = Join-Path $InstallRoot 'bin\ccb.exe'
    if (-not (Test-Path -LiteralPath $launcher -PathType Leaf)) {
        throw "CCB launcher is missing: $launcher"
    }
    $env:PYTHONUTF8 = '1'
    if (-not $env:CCB_STARTUP_TRANSACTION_TIMEOUT_S) {
        $env:CCB_STARTUP_TRANSACTION_TIMEOUT_S = '180'
    }
    Write-Host 'Opening CCB native Windows workspace (first start may take a few minutes)...'
    $launchArguments = @('herdr', 'open', '--wait-ready')
    if ($NoAttach) { $launchArguments += '--no-attach' }
    & $launcher @launchArguments
    if ($LASTEXITCODE -ne 0) {
        throw "CCB returned exit code $LASTEXITCODE. See the diagnostic above; use 'ccb ping all' and 'ccb doctor' for details."
    }
    exit 0
} catch {
    Write-Host "`nCCB could not open: $($_.Exception.Message)" -ForegroundColor Red
    if (-not $NoPause -and [Environment]::UserInteractive) {
        [void](Read-Host 'Press Enter to close this window')
    }
    exit 1
}
