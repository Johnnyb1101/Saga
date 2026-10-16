# Dot-source this file from your PowerShell profile to make saga available.
# Capture only this scope's checkout path, not the caller's LASTEXITCODE.
& {
    $sagaCommandRoot = Split-Path -Parent $PSScriptRoot
    Set-Item -Path Function:\global:saga -Value ({
        $pythonPath = Join-Path $sagaCommandRoot '.venv\Scripts\python.exe'
        $entryPath = Join-Path $sagaCommandRoot 'main.py'
        foreach ($requiredPath in @($pythonPath, $entryPath)) {
            if (-not (Test-Path -LiteralPath $requiredPath -PathType Leaf)) {
                $global:LASTEXITCODE = 1
                throw "Saga cannot start: required file missing: $requiredPath. Check the checkout path and virtual environment."
            }
        }
        & $pythonPath $entryPath @args
        $global:LASTEXITCODE = $LASTEXITCODE
    }.GetNewClosure())
}
