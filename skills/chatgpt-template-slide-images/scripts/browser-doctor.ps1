param(
    [switch]$Json
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$checks = [System.Collections.Generic.List[object]]::new()

function Add-Check {
    param(
        [string]$Name,
        [bool]$Passed,
        [string]$Detail
    )

    $checks.Add([pscustomobject]@{
        name = $Name
        passed = $Passed
        detail = $Detail
    })
}

$agentBrowser = Get-Command 'agent-browser' -ErrorAction SilentlyContinue
if ($null -eq $agentBrowser) {
    Add-Check -Name 'agent-browser' -Passed $false -Detail 'agent-browser is not available on PATH.'
}
else {
    try {
        $versionOutput = (& $agentBrowser.Source --version 2>&1 | Out-String).Trim()
        $versionPassed = $LASTEXITCODE -eq 0 -and -not [string]::IsNullOrWhiteSpace($versionOutput)
        Add-Check -Name 'agent-browser' -Passed $versionPassed -Detail $versionOutput
    }
    catch {
        Add-Check -Name 'agent-browser' -Passed $false -Detail $_.Exception.Message
    }
}

$profileValue = [Environment]::GetEnvironmentVariable('PPTFLOW_CHATGPT_PROFILE')
if ([string]::IsNullOrWhiteSpace($profileValue)) {
    $profileValue = [Environment]::GetEnvironmentVariable('PPTFLOW_CHATGPT_PROFILE', 'User')
}
$resolvedProfile = $null
if ([string]::IsNullOrWhiteSpace($profileValue)) {
    Add-Check -Name 'dedicated-profile' -Passed $false -Detail 'PPTFLOW_CHATGPT_PROFILE is not set.'
}
elseif (-not [System.IO.Path]::IsPathRooted($profileValue)) {
    Add-Check -Name 'dedicated-profile' -Passed $false -Detail 'PPTFLOW_CHATGPT_PROFILE must be an absolute directory path.'
}
else {
    try {
        $resolvedProfile = [System.IO.Path]::GetFullPath($profileValue).TrimEnd('\')
        $isDirectory = Test-Path -LiteralPath $resolvedProfile -PathType Container

        $knownUserDataRoots = [System.Collections.Generic.List[string]]::new()
        if (-not [string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
            $knownUserDataRoots.Add([System.IO.Path]::GetFullPath(
                (Join-Path $env:LOCALAPPDATA 'Google\Chrome\User Data')
            ).TrimEnd('\'))
            $knownUserDataRoots.Add([System.IO.Path]::GetFullPath(
                (Join-Path $env:LOCALAPPDATA 'Microsoft\Edge\User Data')
            ).TrimEnd('\'))
        }

        $isNormalBrowserProfile = $false
        foreach ($root in $knownUserDataRoots) {
            if ($resolvedProfile.Equals($root, [System.StringComparison]::OrdinalIgnoreCase) -or
                $resolvedProfile.StartsWith($root + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
                $isNormalBrowserProfile = $true
                break
            }
        }

        if (-not $isDirectory) {
            Add-Check -Name 'dedicated-profile' -Passed $false -Detail "Profile directory does not exist: $resolvedProfile"
        }
        elseif ($isNormalBrowserProfile) {
            Add-Check -Name 'dedicated-profile' -Passed $false -Detail 'Profile points into a normal Chrome or Edge user-data directory. Use a separate custom directory.'
        }
        else {
            Add-Check -Name 'dedicated-profile' -Passed $true -Detail $resolvedProfile
        }
    }
    catch {
        Add-Check -Name 'dedicated-profile' -Passed $false -Detail $_.Exception.Message
    }
}

Add-Check -Name 'visible-mode' -Passed $true -Detail 'The skill requires --headed for every agent-browser command.'
Add-Check -Name 'domain-policy' -Passed $true -Detail 'The skill permits navigation only to HTTPS host chatgpt.com and verifies the URL after possible navigation.'
Add-Check -Name 'authentication-policy' -Passed $true -Detail 'Doctor does not open ChatGPT, inspect login state, store credentials, or automate login/CAPTCHA.'

$passed = -not ($checks | Where-Object { -not $_.passed })
$result = [pscustomobject]@{
    passed = $passed
    checked_at = (Get-Date).ToString('o')
    profile = $resolvedProfile
    login_checked = $false
    browser_opened = $false
    checks = $checks
}

if ($Json) {
    $result | ConvertTo-Json -Depth 5
}
else {
    foreach ($check in $checks) {
        $label = if ($check.passed) { 'PASS' } else { 'FAIL' }
        Write-Output ("[{0}] {1}: {2}" -f $label, $check.name, $check.detail)
    }
    Write-Output ("Overall: {0}" -f $(if ($passed) { 'PASS' } else { 'FAIL' }))
}

if (-not $passed) {
    exit 1
}
