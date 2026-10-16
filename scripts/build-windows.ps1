param(
    [string]$Python = (Join-Path $PSScriptRoot '..\.build-venv\Scripts\python.exe')
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$pythonPath = (Resolve-Path -LiteralPath $Python).Path
& $pythonPath -c 'import sys; sys.exit(sys.version_info[:2] != (3, 15))'
if ($LASTEXITCODE -ne 0) {
    throw 'Build Saga with Python 3.15. Recreate .build-venv using Python 3.15 first.'
}
$distPath = Join-Path $repoRoot 'dist'
$workPath = Join-Path $repoRoot 'build'

# Other tools can put incompatible Windows DLLs on PATH. Build using only the
# chosen Python and Windows folders, then restore the caller's environment.
$originalPath = $env:PATH
try {
    $env:PATH = (Split-Path -Parent $pythonPath) + ';' + (Join-Path $env:SystemRoot 'System32') + ';' + $env:SystemRoot
    & $pythonPath -m PyInstaller --clean --noconfirm --distpath $distPath --workpath $workPath (Join-Path $repoRoot 'Saga.spec')
    if ($LASTEXITCODE -ne 0) { throw "Saga build failed ($LASTEXITCODE)." }
} finally {
    $env:PATH = $originalPath
}

# A windowed executable has no console: wait for its exit and read its report.
$reportPath = Join-Path $workPath ('smoke-' + [guid]::NewGuid().ToString('N') + '.json')
$appPath = Join-Path $distPath 'Saga\Saga.exe'
$check = Start-Process -FilePath $appPath -ArgumentList @('--smoke-test', ('"' + $reportPath + '"')) -WorkingDirectory $env:TEMP -WindowStyle Hidden -PassThru
if (-not $check.WaitForExit(120000)) {
    $check.Kill()
    throw 'Packaged Saga check timed out.'
}
if ($check.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $reportPath)) {
    throw "Packaged Saga check failed. Report: $reportPath"
}
$report = Get-Content -LiteralPath $reportPath -Raw | ConvertFrom-Json
if (-not $report.success -or -not $report.frozen -or $report.runtime.python -notmatch '^3\.15\.') {
    throw "Invalid packaged check report or Python runtime: $reportPath"
}
Write-Output "Built and checked: $appPath"
Write-Output "Check report: $reportPath"
