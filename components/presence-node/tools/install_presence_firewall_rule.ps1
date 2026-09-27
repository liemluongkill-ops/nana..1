param(
    [string]$PythonPath = "<USER_HOME>\AppData\Local\Programs\Python\Python311\python.exe",
    [string]$RemoteSubnet = "<LOCAL_IP>/24",
    [int]$Port = 8765
)

$ErrorActionPreference = "Stop"
$ruleName = "Nana Presence Session V1"
$logPath = Join-Path $PSScriptRoot "presence_firewall_install.log"

Set-Content -LiteralPath $logPath -Value "Nana Presence firewall installer"

function Write-InstallLog {
    param([string]$Message)
    Write-Host $Message
    Add-Content -LiteralPath $logPath -Value $Message
}

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Run this script from an elevated PowerShell window."
}

$resolvedPython = (Resolve-Path -LiteralPath $PythonPath).Path

# Windows creates broad per-program rules from its first-listen prompt. They may
# be Block or Allow depending on the prompt response, and either form conflicts
# with Nana's intentionally port- and subnet-scoped policy. Disable only those
# auto-generated rules for this exact Python executable before adding Nana's
# dedicated exception.
$broadPythonRules = Get-NetFirewallRule -DisplayName "python.exe" -ErrorAction SilentlyContinue |
    Where-Object {
        $_.Direction -eq "Inbound" -and
        $_.Name -like "*Query User*"
    } |
    Where-Object {
        $application = $_ | Get-NetFirewallApplicationFilter
        $application.Program -ieq $resolvedPython
    }

try {
    foreach ($rule in $broadPythonRules) {
        $rule | Disable-NetFirewallRule | Out-Null
        Write-InstallLog "Disabled broad Python inbound rule: $($rule.Name)"
    }

    Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue |
        Remove-NetFirewallRule

    New-NetFirewallRule `
        -DisplayName $ruleName `
        -Description "Allow authenticated Nana Presence ESP32 sessions from the home LAN only." `
        -Direction Inbound `
        -Action Allow `
        -Enabled True `
        -Profile Public,Private `
        -Program $resolvedPython `
        -Protocol TCP `
        -LocalPort $Port `
        -RemoteAddress $RemoteSubnet | Out-Null

    Write-InstallLog "Installed $ruleName"
    Write-InstallLog "Program: $resolvedPython"
    Write-InstallLog "TCP port: $Port"
    Write-InstallLog "Remote subnet: $RemoteSubnet"
    Write-InstallLog "Result: PASS"
} catch {
    Write-InstallLog "Result: FAIL"
    Write-InstallLog $_.Exception.ToString()
    exit 1
}
