param(
    [Parameter(Mandatory)][ValidateSet('Start','Stop','Inspect')][string]$Action,
    [Parameter(Mandatory)][string]$Config
)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
$setup = Get-Content -LiteralPath $Config -Raw -Encoding UTF8 | ConvertFrom-Json
$exe = (Get-Item -LiteralPath $setup.executable).FullName
if ([IO.Path]::GetExtension($exe) -ne '.exe') { throw 'Select the installed ONELAB executable.' }
if ([string]::IsNullOrWhiteSpace($setup.window_title)) { throw 'Set the exact ONELAB window title.' }

function Get-Onelab {
    Get-Process -Name ([IO.Path]::GetFileNameWithoutExtension($exe)) -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -eq $exe }
}

if ($Action -eq 'Start' -and @(Get-Onelab).Count -eq 0) {
    Start-Process -FilePath $exe -WorkingDirectory ([IO.Path]::GetDirectoryName($exe)) -WindowStyle Normal
}

$selector = if ($Action -eq 'Start') { $setup.start } else { $setup.stop }
if ($Action -ne 'Inspect' -and [string]::IsNullOrWhiteSpace($selector.automation_id) -and
    [string]::IsNullOrWhiteSpace($selector.name)) { throw "Set the ONELAB $Action control selector." }

$watch = [Diagnostics.Stopwatch]::StartNew()
$root = [Windows.Automation.AutomationElement]::RootElement
$windowMatch = [Windows.Automation.PropertyCondition]::new(
    [Windows.Automation.AutomationElement]::NameProperty, [string]$setup.window_title)
while ($watch.Elapsed.TotalSeconds -lt 6) {
    $ids = @(Get-Onelab | Select-Object -ExpandProperty Id)
    $windows = @($root.FindAll([Windows.Automation.TreeScope]::Children, $windowMatch) |
        Where-Object { $_.Current.ProcessId -in $ids })
    if ($windows.Count -gt 1) { throw 'More than one ONELAB window matched.' }
    if ($windows.Count -eq 1) {
        $buttonMatch = [Windows.Automation.PropertyCondition]::new(
            [Windows.Automation.AutomationElement]::ControlTypeProperty, [Windows.Automation.ControlType]::Button)
        $buttons = $windows[0].FindAll([Windows.Automation.TreeScope]::Descendants, $buttonMatch)
        if ($Action -eq 'Inspect') {
            $buttons | ForEach-Object {
                [pscustomobject]@{Name=$_.Current.Name; AutomationId=$_.Current.AutomationId; Enabled=$_.Current.IsEnabled}
            } | Format-Table -AutoSize
            return
        }
        $matches = @($buttons | Where-Object {
            ([string]::IsNullOrWhiteSpace($selector.automation_id) -or $_.Current.AutomationId -ceq $selector.automation_id) -and
            ([string]::IsNullOrWhiteSpace($selector.name) -or $_.Current.Name -ceq $selector.name)
        })
        if ($matches.Count -gt 1) { throw "More than one ONELAB $Action button matched." }
        if ($matches.Count -eq 1 -and $matches[0].Current.IsEnabled) {
            $pattern = $matches[0].GetCurrentPattern([Windows.Automation.InvokePattern]::Pattern)
            $pattern.Invoke()
            return
        }
    }
    Start-Sleep -Milliseconds 150
}
throw "ONELAB $Action control unavailable. Check the window title, recording setup, and radar connection."
