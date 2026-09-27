<#
Open the OBS-selected Nana Voice Host console.
Examples:
  powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\start_stream_voice_host.ps1 -Demo
  powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\start_stream_voice_host.ps1 'https://youtube.com/live/VIDEO_ID'
No argument prompts for a link inside the new window. Ctrl+C requests graceful
Nana stop; OBS streaming stays under operator control.
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)][string]$Video = '',
    [switch]$Demo,
    [ValidateRange(1, 2147483647)][int]$MaxTurns = 50,
    [ValidateRange(0.01, 10080)][double]$Minutes = 30,
    [ValidateRange(1, 2147483647)][int]$MaxPolls = 3600,
    [ValidateSet('api-key', 'oauth', 'oauth-cache')][string]$ReadAuth = 'api-key',
    [string]$PythonPath = '',
    [switch]$InCurrentWindow,
    [switch]$NoPause
)

$ErrorActionPreference = 'Stop'
$sessionRoot = Split-Path -Parent $PSScriptRoot
if (-not $PythonPath) {
    $PythonPath = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python311\python.exe'
}
if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    throw 'Python 3.11 audio runtime not found. Supply -PythonPath with the existing Nana audio interpreter.'
}

if (-not $InCurrentWindow) {
    # User input is encoded as JSON data, never interpolated as command text.
    $sessionOptions = @{
        Video = $Video; Demo = [bool]$Demo; MaxTurns = $MaxTurns;
        Minutes = $Minutes; MaxPolls = $MaxPolls; ReadAuth = $ReadAuth;
        PythonPath = $PythonPath; NoPause = [bool]$NoPause
    }
    $sessionJson = $sessionOptions | ConvertTo-Json -Compress
    $sessionData = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($sessionJson))
    $sessionScript = $PSCommandPath.Replace("'", "''")
    $sessionCommand = '$sessionObject = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String(''' + $sessionData + ''')) | ConvertFrom-Json; $sessionParameters = @{}; $sessionObject.PSObject.Properties | ForEach-Object { $sessionParameters[$_.Name] = $_.Value }; & ''' + $sessionScript + ''' @sessionParameters -InCurrentWindow'
    $sessionEncoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($sessionCommand))
    $sessionProcess = Start-Process -FilePath (Join-Path $PSHOME 'powershell.exe') `
        -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-EncodedCommand', $sessionEncoded) `
        -WorkingDirectory $sessionRoot -PassThru
    Write-Output ('Opened Nana Voice Host (PID {0}). Use Ctrl+C in that window to stop Nana.' -f $sessionProcess.Id)
    return
}

$sessionOldTitle = $Host.UI.RawUI.WindowTitle
$sessionOldEncoding = [Console]::OutputEncoding
$sessionOldUtf8 = [Environment]::GetEnvironmentVariable('PYTHONUTF8', 'Process')
$sessionExitCode = 2
try {
    $Host.UI.RawUI.WindowTitle = 'Nana Voice Host'
    [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
    $env:PYTHONUTF8 = '1'
    $sessionArguments = @('-u', '-B', (Join-Path $PSScriptRoot 'start_stream_voice_session.py'))
    if ($Video) { $sessionArguments += $Video }
    if ($Demo) { $sessionArguments += '--demo' }
    $sessionArguments += @('--max-turns', [string]$MaxTurns, '--minutes', $Minutes.ToString([Globalization.CultureInfo]::InvariantCulture),
                           '--max-polls', [string]$MaxPolls, '--read-auth', $ReadAuth)
    & $PythonPath @sessionArguments
    $sessionExitCode = $LASTEXITCODE
} finally {
    if ($null -eq $sessionOldUtf8) { Remove-Item Env:PYTHONUTF8 -ErrorAction SilentlyContinue }
    else { $env:PYTHONUTF8 = $sessionOldUtf8 }
    [Console]::OutputEncoding = $sessionOldEncoding
    $Host.UI.RawUI.WindowTitle = $sessionOldTitle
}
if (-not $NoPause) {
    Write-Host ('Nana da dung (exit {0}). OBS van do ong dieu khien.' -f $sessionExitCode)
    $null = Read-Host 'Nhan Enter de dong cua so'
}
exit $sessionExitCode
