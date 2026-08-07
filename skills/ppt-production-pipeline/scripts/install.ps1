[CmdletBinding()]
param(
    [string]$SkillHome,
    [string]$ChatGPTProfile = (Join-Path $env:USERPROFILE '.pptflow\chatgpt-profile')
)

$ErrorActionPreference = 'Stop'
if ([string]::IsNullOrWhiteSpace($SkillHome)) {
    $SkillHome = Split-Path -Parent $PSScriptRoot
}
$skillPath = (Resolve-Path -LiteralPath $SkillHome).Path
$cliPath = Join-Path $skillPath 'scripts\pptflow.py'
if (-not (Test-Path -LiteralPath $cliPath -PathType Leaf)) {
    throw "pptflow.py was not found at $cliPath"
}
if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
    throw 'The Windows py launcher is required by pptflow.cmd.'
}
& py -3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)'
$pythonExitCode = $LASTEXITCODE
if ($pythonExitCode -ne 0) {
    throw 'Python 3.10 or newer must be available through `py -3`.'
}

$binPath = Join-Path $env:USERPROFILE '.local\bin'
New-Item -ItemType Directory -Path $binPath -Force | Out-Null
New-Item -ItemType Directory -Path $ChatGPTProfile -Force | Out-Null

$wrapperPath = Join-Path $binPath 'pptflow.cmd'
$wrapper = @"
@echo off
set "PPTFLOW_SKILL_HOME=$skillPath"
set "PPTFLOW_CHATGPT_PROFILE=$ChatGPTProfile"
py -3 -X utf8 "$cliPath" %*
"@
[IO.File]::WriteAllText($wrapperPath, $wrapper, [Text.UTF8Encoding]::new($false))

$userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
$parts = @($userPath -split ';' | Where-Object { $_ })
if (-not ($parts | Where-Object { $_.TrimEnd('\') -ieq $binPath.TrimEnd('\') })) {
    $newPath = if ($userPath) { "$userPath;$binPath" } else { $binPath }
    [Environment]::SetEnvironmentVariable('Path', $newPath, 'User')
}
[Environment]::SetEnvironmentVariable('PPTFLOW_SKILL_HOME', $skillPath, 'User')
[Environment]::SetEnvironmentVariable('PPTFLOW_CHATGPT_PROFILE', $ChatGPTProfile, 'User')
$env:PPTFLOW_SKILL_HOME = $skillPath
$env:PPTFLOW_CHATGPT_PROFILE = $ChatGPTProfile
$env:Path = "$binPath;$env:Path"

$doctor = & $wrapperPath --json doctor
if ($LASTEXITCODE -ne 0) {
    throw "pptflow doctor failed after installation: $doctor"
}

[ordered]@{
    installed = $true
    command = $wrapperPath
    skill_home = $skillPath
    chatgpt_profile = $ChatGPTProfile
    doctor = ($doctor | ConvertFrom-Json)
} | ConvertTo-Json -Depth 8
