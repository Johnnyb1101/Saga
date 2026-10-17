"""Exercise the installer's scheduled-task update without touching Task Scheduler."""

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts" / "install-windows.ps1"
POWERSHELL = shutil.which("powershell.exe")

# Parse the real installer, then load only the function under test. Running the whole
# installer would copy files, launch Saga, and change shortcuts on this machine.
HARNESS = r"""
param([string]$InstallerPath, [string]$Mode)
$ErrorActionPreference = 'Stop'
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    $InstallerPath, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count -ne 0) { throw "Installer parse failed: $($parseErrors[0])" }
$definitions = @($ast.FindAll({
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -eq 'Update-SagaMorningTask'
}, $true))
if ($definitions.Count -ne 1) { throw "Expected one Update-SagaMorningTask function" }
. ([scriptblock]::Create($definitions[0].Extent.Text))

$oldAction = [pscustomobject]@{
    Execute = 'C:\old\Saga.exe'
    Arguments = '--morning'
    WorkingDirectory = 'C:\old'
}
$task = [pscustomobject]@{
    TaskName = 'Saga Morning Brief'
    TaskPath = '\'
    Actions = @($oldAction)
    Triggers = @('original trigger')
    Settings = 'original settings'
    Principal = 'original principal'
}
if ($Mode -eq 'multi_action') {
    $task.Actions = @($oldAction, [pscustomobject]@{
        Execute = 'C:\old\second.exe'
        Arguments = ''
        WorkingDirectory = 'C:\old'
    })
}
$calls = [System.Collections.Generic.List[object]]::new()
$setCount = 0

function Get-ScheduledTask {
    param($TaskName, $TaskPath, $ErrorAction)
    $calls.Add([pscustomobject]@{
        Command = 'Get-ScheduledTask'; TaskName = $TaskName; TaskPath = $TaskPath
    })
    if ($Mode -eq 'lookup_denied') { throw 'task lookup denied' }
    if ($Mode -eq 'absent') { return }
    return $task
}
function New-ScheduledTaskAction {
    param($Execute, $Argument, $WorkingDirectory)
    $calls.Add([pscustomobject]@{ Command = 'New-ScheduledTaskAction' })
    return [pscustomobject]@{
        Execute = $Execute; Arguments = $Argument; WorkingDirectory = $WorkingDirectory
    }
}
function Set-ScheduledTask {
    param($TaskName, $TaskPath, $InputObject, $Action, $ErrorAction)
    $script:setCount++
    $calls.Add([pscustomobject]@{
        Command = 'Set-ScheduledTask'; TaskName = $TaskName; TaskPath = $TaskPath
        Action = @($Action | ForEach-Object {
            [pscustomobject]@{
                Execute = $_.Execute
                Arguments = $_.Arguments
                WorkingDirectory = $_.WorkingDirectory
            }
        })
    })
    # Model a failure after the scheduler has accepted a replacement action.
    $task.Actions = @($Action)
    if ($Mode -in @('update_fails', 'rollback_fails') -and $script:setCount -eq 1) {
        throw 'task update denied'
    }
    if ($Mode -eq 'rollback_fails' -and $script:setCount -eq 2) {
        throw 'task rollback denied'
    }
}
function Register-ScheduledTask { throw 'unexpected registration' }
function Unregister-ScheduledTask { throw 'unexpected unregistration' }

$failure = $null
try {
    Update-SagaMorningTask -AppPath 'C:\new install\Saga.exe' -Destination 'C:\new install' |
        Out-Null
} catch {
    $failure = $_.Exception.Message
}
[pscustomobject]@{
    Error = $failure
    Calls = @($calls.ToArray())
    Actions = @($task.Actions)
    Triggers = @($task.Triggers)
    Settings = $task.Settings
    Principal = $task.Principal
} | ConvertTo-Json -Depth 8 -Compress
"""

SHORTCUT_ROLLBACK_HARNESS = r"""
param([string]$InstallerPath, [string]$Mode)
$ErrorActionPreference = 'Stop'
$script:mode = $Mode
$script:taskCalled = 0
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    $InstallerPath, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count -ne 0) { throw "Installer parse failed: $($parseErrors[0])" }
$blocks = @($ast.FindAll({
    param($node)
    $node -is [System.Management.Automation.Language.TryStatementAst] -and
        $node.Extent.Text.Contains('Update-SagaMorningTask -AppPath $appPath')
}, $true))
if ($blocks.Count -ne 1) { throw 'Expected one shortcut/task update block' }

$first = Join-Path $PSScriptRoot 'desktop.lnk'
$second = Join-Path $PSScriptRoot 'programs.lnk'
[IO.File]::WriteAllBytes($first, [byte[]](1, 2, 3))
[IO.File]::WriteAllBytes($second, [byte[]](4, 5, 6))
$previousShortcuts = @{
    $first = [IO.File]::ReadAllBytes($first)
    $second = [IO.File]::ReadAllBytes($second)
}
$appPath = 'C:\new install\Saga.exe'
$destination = 'C:\new install'
$shell = [pscustomobject]@{}
$shell | Add-Member -MemberType ScriptMethod -Name CreateShortcut -Value {
    param($path)
    $shortcut = [pscustomobject]@{
        Path = $path; TargetPath = ''; Arguments = ''; WorkingDirectory = ''
        Description = ''; IconLocation = ''
    }
    $shortcut | Add-Member -MemberType ScriptMethod -Name Save -Value {
        if ($script:mode -eq 'save_failure') { throw 'mock shortcut save failed' }
        [IO.File]::WriteAllBytes($this.Path, [byte[]](9, 9, 9))
    }
    return $shortcut
}
function Update-SagaMorningTask {
    $script:taskCalled++
    throw 'mock task update failed'
}
$failure = $null
try {
    . ([scriptblock]::Create($blocks[0].Extent.Text)) | Out-Null
} catch {
    $failure = $_.Exception.Message
}
[pscustomobject]@{
    Error = $failure
    First = @([IO.File]::ReadAllBytes($first))
    Second = @([IO.File]::ReadAllBytes($second))
    TaskCalled = $script:taskCalled
} | ConvertTo-Json -Depth 5 -Compress
"""


@unittest.skipUnless(POWERSHELL, "Windows PowerShell is required")
class InstallMorningTaskTests(unittest.TestCase):
    def run_harness(self, script, *arguments):
        with tempfile.TemporaryDirectory() as directory:
            harness = Path(directory) / "task-harness.ps1"
            harness.write_text(script, encoding="utf-8")
            result = subprocess.run(
                [POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                 "-File", str(harness), "-InstallerPath", str(INSTALLER), *arguments],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        return json.loads(result.stdout)

    def run_case(self, mode):
        return self.run_harness(HARNESS, "-Mode", mode)

    @staticmethod
    def calls(data, command):
        return [call for call in data["Calls"] if call["Command"] == command]

    def test_absent_task_is_not_created(self):
        data = self.run_case("absent")
        self.assertIsNone(data["Error"])
        self.assertEqual(len(self.calls(data, "Get-ScheduledTask")), 1)
        self.assertEqual(self.calls(data, "Set-ScheduledTask"), [])
        self.assertEqual(self.calls(data, "New-ScheduledTaskAction"), [])

    def test_existing_task_updates_only_its_single_action(self):
        data = self.run_case("success")
        self.assertIsNone(data["Error"])
        lookup = self.calls(data, "Get-ScheduledTask")
        self.assertEqual(len(lookup), 1)
        self.assertEqual(lookup[0]["TaskPath"], "\\")
        updates = self.calls(data, "Set-ScheduledTask")
        self.assertEqual(len(updates), 1)
        self.assertEqual(updates[0]["TaskName"], "Saga Morning Brief")
        self.assertEqual(updates[0]["TaskPath"], "\\")
        self.assertEqual(data["Actions"][0]["Execute"], "C:\\new install\\Saga.exe")
        self.assertIn(data["Actions"][0]["Arguments"], (None, ""))
        self.assertEqual(data["Actions"][0]["WorkingDirectory"], "C:\\new install")
        self.assertEqual(data["Triggers"], ["original trigger"])
        self.assertEqual(data["Settings"], "original settings")
        self.assertEqual(data["Principal"], "original principal")

    def test_lookup_error_is_reported_without_update(self):
        data = self.run_case("lookup_denied")
        self.assertIn("task lookup denied", data["Error"])
        self.assertEqual(self.calls(data, "Set-ScheduledTask"), [])

    def test_failed_update_restores_saved_action(self):
        data = self.run_case("update_fails")
        self.assertIn("task update denied", data["Error"])
        self.assertEqual(len(self.calls(data, "Set-ScheduledTask")), 2)
        self.assertEqual(data["Actions"], [{
            "Execute": "C:\\old\\Saga.exe",
            "Arguments": "--morning",
            "WorkingDirectory": "C:\\old",
        }])

    def test_failed_rollback_reports_both_failures(self):
        data = self.run_case("rollback_fails")
        self.assertIn("task update denied", data["Error"])
        self.assertIn("task rollback denied", data["Error"])
        self.assertEqual(len(self.calls(data, "Set-ScheduledTask")), 2)

    def test_multiple_actions_are_refused_before_update(self):
        data = self.run_case("multi_action")
        self.assertIsNotNone(data["Error"])
        self.assertEqual(self.calls(data, "Set-ScheduledTask"), [])
        self.assertEqual(len(data["Actions"]), 2)

    def test_task_failure_restores_both_shortcuts(self):
        data = self.run_harness(SHORTCUT_ROLLBACK_HARNESS, "-Mode", "task_failure")
        self.assertIn("mock task update failed", data["Error"])
        self.assertEqual(data["TaskCalled"], 1)
        self.assertEqual(data["First"], [1, 2, 3])
        self.assertEqual(data["Second"], [4, 5, 6])

    def test_shortcut_failure_leaves_task_untouched(self):
        data = self.run_harness(SHORTCUT_ROLLBACK_HARNESS, "-Mode", "save_failure")
        self.assertIn("mock shortcut save failed", data["Error"])
        self.assertEqual(data["TaskCalled"], 0)
        self.assertEqual(data["First"], [1, 2, 3])
        self.assertEqual(data["Second"], [4, 5, 6])


if __name__ == "__main__":
    unittest.main()
