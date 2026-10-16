param(
    [string]$Bundle = (Join-Path $PSScriptRoot '..\dist\Saga'),
    [string]$InstallRoot = (Join-Path $env:LOCALAPPDATA 'Programs\Saga'),
    [switch]$Launch
)

$ErrorActionPreference = 'Stop'
$bundlePath = (Resolve-Path -LiteralPath $Bundle).Path
if (-not (Test-Path -LiteralPath (Join-Path $bundlePath 'Saga.exe') -PathType Leaf)) {
    throw "No Saga.exe in $bundlePath. Build Saga first."
}

# Every install gets its own folder. Existing versions and archives are preserved.
$version = (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [guid]::NewGuid().ToString('N').Substring(0, 8)
$destination = Join-Path ([IO.Path]::GetFullPath($InstallRoot)) $version
if ($destination.StartsWith($bundlePath.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) {
    throw 'The installation folder must be outside the source bundle.'
}
New-Item -ItemType Directory -Path $destination | Out-Null
Get-ChildItem -LiteralPath $bundlePath -Force | Copy-Item -Destination $destination -Recurse
$appPath = Join-Path $destination 'Saga.exe'
$reportPath = Join-Path $destination 'installation-check.json'
$check = Start-Process -FilePath $appPath -ArgumentList @('--smoke-test', ('"' + $reportPath + '"')) -WorkingDirectory $env:TEMP -WindowStyle Hidden -PassThru
if (-not $check.WaitForExit(120000)) {
    $check.Kill()
    throw 'Installed Saga check timed out; shortcuts were not changed.'
}
if ($check.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $reportPath)) {
    throw "Installed Saga check failed; shortcuts were not changed. Report: $reportPath"
}
$report = Get-Content -LiteralPath $reportPath -Raw | ConvertFrom-Json
if (-not $report.success -or -not $report.frozen -or $report.runtime.python -notmatch '^3\.15\.') {
    throw "Invalid installed check report; shortcuts were not changed: $reportPath"
}

$shell = New-Object -ComObject WScript.Shell
$shortcutFolders = @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))
$previousShortcuts = @{}
foreach ($folder in $shortcutFolders) {
    if (-not $folder) { throw 'Windows did not provide a shortcut folder.' }
    $shortcutPath = Join-Path $folder 'Saga.lnk'
    $previousShortcuts[$shortcutPath] = if (Test-Path -LiteralPath $shortcutPath) {
        [IO.File]::ReadAllBytes($shortcutPath)
    } else { $null }
}
try {
    foreach ($shortcutPath in $previousShortcuts.Keys) {
        $shortcut = $shell.CreateShortcut($shortcutPath)
        $shortcut.TargetPath = $appPath
        $shortcut.Arguments = ''
        $shortcut.WorkingDirectory = $destination
        $shortcut.Description = 'Saga - your local tasks and accomplishment archive'
        $shortcut.IconLocation = "$appPath,0"
        $shortcut.Save()
        Write-Output "Shortcut: $shortcutPath"
    }
} catch {
    # Restore both original shortcuts if either update fails.
    foreach ($shortcutPath in $previousShortcuts.Keys) {
        if ($null -ne $previousShortcuts[$shortcutPath]) {
            [IO.File]::WriteAllBytes($shortcutPath, $previousShortcuts[$shortcutPath])
        } elseif (Test-Path -LiteralPath $shortcutPath) {
            Remove-Item -LiteralPath $shortcutPath
        }
    }
    throw
}
Write-Output "Installed and checked: $appPath"
Write-Output 'Your archive and settings remain in their existing locations.'
if ($Launch) { Start-Process -FilePath $appPath -WorkingDirectory $destination }
