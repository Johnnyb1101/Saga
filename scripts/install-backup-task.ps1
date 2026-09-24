[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter(Mandatory)][string]$Destination,
    [Parameter(Mandatory)][ValidatePattern('^([01][0-9]|2[0-3]):[0-5][0-9]$')][string]$At,
    [ValidateRange(1, 2147483647)][int]$Keep,
    [string]$TaskName = 'Saga Scheduled Backup'
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv\Scripts\pythonw.exe'
$runnerPath = Join-Path $projectRoot 'scheduled_backup.py'
$databasePath = Join-Path $projectRoot 'data\saga.db'
foreach ($requiredPath in @($pythonPath, $runnerPath, $databasePath)) {
    if (-not (Test-Path -LiteralPath $requiredPath -PathType Leaf)) {
        throw "Required file missing: $requiredPath"
    }
}
$destinationPath = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Destination)
# Appending a child prevents a trailing backslash from escaping the closing quote.
$destinationPath = Join-Path $destinationPath '.'
foreach ($argumentPath in @($runnerPath, $databasePath, $destinationPath)) {
    if ($argumentPath.Contains('"') -or $argumentPath.Contains("`n") -or $argumentPath.Contains("`r")) {
        throw 'Paths cannot contain quotes or newlines.'
    }
}
$arguments = '"{0}" --db "{1}" --out "{2}"' -f $runnerPath, $databasePath, $destinationPath
if ($PSBoundParameters.ContainsKey('Keep')) {
    $arguments += " --keep $Keep"
}
$action = New-ScheduledTaskAction -Execute $pythonPath -Argument $arguments -WorkingDirectory $projectRoot
$trigger = New-ScheduledTaskTrigger -Daily -At ([datetime]::ParseExact($At, 'HH:mm', [cultureinfo]::InvariantCulture))
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 1)
$principal = New-ScheduledTaskPrincipal -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) `
    -LogonType Interactive -RunLevel Limited
if ($PSCmdlet.ShouldProcess($TaskName, "Register daily backup at $At to $destinationPath")) {
    # No -Force: an existing task must not be silently replaced.
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
        -Settings $settings -Principal $principal -Description 'Saga verified backup and restore check'
}
