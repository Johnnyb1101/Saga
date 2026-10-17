param(
    [string]$Bundle = (Join-Path $PSScriptRoot '..\dist\Saga'),
    [string]$InstallRoot = (Join-Path $env:LOCALAPPDATA 'Programs\Saga'),
    [switch]$Launch
)

$ErrorActionPreference = 'Stop'

function Update-SagaMorningTask {
    param([string]$AppPath, [string]$Destination)

    # Enumerate the root folder so a missing task is distinct from a lookup
    # failure, such as permission being denied. Never create a schedule here.
    $tasks = @(Get-ScheduledTask -TaskPath '\' -ErrorAction Stop |
        Where-Object { $_.TaskName -eq 'Saga Morning Brief' })
    if ($tasks.Count -eq 0) {
        Write-Output 'No Saga Morning Brief task to update.'
        return
    }
    if ($tasks.Count -ne 1 -or @($tasks[0].Actions).Count -ne 1) {
        throw 'Saga Morning Brief must have exactly one launch action; it was not changed.'
    }
    $previousActions = @($tasks[0].Actions)
    $action = New-ScheduledTaskAction -Execute $AppPath -WorkingDirectory $Destination
    try {
        Set-ScheduledTask -TaskName 'Saga Morning Brief' -TaskPath '\' -Action $action -ErrorAction Stop | Out-Null
    } catch {
        $updateFailure = $_
        try {
            # Restore even when a provider reports failure after applying a change.
            Set-ScheduledTask -TaskName 'Saga Morning Brief' -TaskPath '\' -Action $previousActions -ErrorAction Stop | Out-Null
        } catch {
            throw "Could not update Saga Morning Brief: $($updateFailure.Exception.Message). Could not restore its previous action: $($_.Exception.Message)."
        }
        throw "Could not update Saga Morning Brief; its previous action was restored: $($updateFailure.Exception.Message)"
    }
    Write-Output "Morning task updated: $AppPath"
}

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
    # Updating only the action preserves triggers, account, and task settings.
    # Do this last; a task-update failure also rolls the shortcuts back below.
    Update-SagaMorningTask -AppPath $appPath -Destination $destination
} catch {
    $launchFailure = $_
    $restoreErrors = @()
    # Try both restorations even if one fails, and report every failure.
    foreach ($shortcutPath in $previousShortcuts.Keys) {
        try {
            if ($null -ne $previousShortcuts[$shortcutPath]) {
                [IO.File]::WriteAllBytes($shortcutPath, $previousShortcuts[$shortcutPath])
            } elseif (Test-Path -LiteralPath $shortcutPath) {
                Remove-Item -LiteralPath $shortcutPath
            }
        } catch {
            $restoreErrors += "$shortcutPath`: $($_.Exception.Message)"
        }
    }
    if ($restoreErrors.Count -gt 0) {
        throw "Could not finish updating Saga launch settings: $($launchFailure.Exception.Message). Shortcut restoration also failed: $($restoreErrors -join '; ')"
    }
    throw $launchFailure
}
Write-Output "Installed and checked: $appPath"
Write-Output 'Your archive and settings remain in their existing locations.'
if ($Launch) { Start-Process -FilePath $appPath -WorkingDirectory $destination }
