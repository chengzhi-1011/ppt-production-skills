[CmdletBinding()]
param(
    [string]$DestinationRoot = (Join-Path $env:USERPROFILE '.codex\skills'),
    [switch]$ConfirmRemoval
)

$ErrorActionPreference = 'Stop'
if (-not $ConfirmRemoval) {
    throw 'Uninstall is recoverable but requires -ConfirmRemoval.'
}

$packageId = 'chengzhi-1011/ppt-production-skills'
$skillNames = @(
    'ppt-production-pipeline',
    'ppt-material-to-outline',
    'chatgpt-template-slide-images',
    'image-to-editable-ppt'
)
$DestinationRoot = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($DestinationRoot)
$manifestPath = Join-Path $DestinationRoot '.ppt-production-skills-install.json'
if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
    throw "Installation manifest is missing; refusing to move unowned Skill directories: $manifestPath"
}

$manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ($manifest.package -ne $packageId -or -not $manifest.installation_id) {
    throw 'Installation manifest ownership is invalid.'
}
$recordedRoot = [IO.Path]::GetFullPath([string]$manifest.destination_root).TrimEnd('\')
if (-not $recordedRoot.Equals($DestinationRoot.TrimEnd('\'), [StringComparison]::OrdinalIgnoreCase)) {
    throw 'DestinationRoot does not match the owned installation manifest.'
}
$recordedSkills = @($manifest.skills)
if ($recordedSkills.Count -ne $skillNames.Count -or @($skillNames | Where-Object { $_ -notin $recordedSkills }).Count -ne 0) {
    throw 'Installation manifest Skill set is invalid.'
}

# Validate ownership of every target before moving anything.
foreach ($name in $skillNames) {
    $source = Join-Path $DestinationRoot $name
    $ownerPath = Join-Path $source '.ppt-production-skills-owner.json'
    if (-not (Test-Path -LiteralPath $source -PathType Container) -or
        -not (Test-Path -LiteralPath $ownerPath -PathType Leaf)) {
        throw "Owned Skill marker is missing: $ownerPath"
    }
    $owner = Get-Content -LiteralPath $ownerPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($owner.package -ne $packageId -or
        $owner.installation_id -ne $manifest.installation_id -or
        $owner.skill -ne $name) {
        throw "Owned Skill marker is invalid: $ownerPath"
    }
}

$wrapperPath = [string]$manifest.wrapper_path
$expectedWrapperPath = Join-Path $env:USERPROFILE '.local\bin\pptflow.cmd'
if ($wrapperPath -and -not ([IO.Path]::GetFullPath($wrapperPath).Equals(
    [IO.Path]::GetFullPath($expectedWrapperPath),
    [StringComparison]::OrdinalIgnoreCase
))) {
    throw 'Installation manifest wrapper path is outside the managed location.'
}
$wrapperOwned = $false
if ($wrapperPath -and (Test-Path -LiteralPath $wrapperPath -PathType Leaf)) {
    $wrapperHash = (Get-FileHash -LiteralPath $wrapperPath -Algorithm SHA256).Hash.ToLowerInvariant()
    $wrapperOwned = $wrapperHash -eq [string]$manifest.wrapper_sha256
}

$stamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
$backupRoot = Join-Path $env:USERPROFILE ".ppt-production-skills-backup\uninstall-$stamp-$([guid]::NewGuid().ToString('N'))"
$moves = [System.Collections.Generic.List[object]]::new()
$priorSkillHome = [Environment]::GetEnvironmentVariable('PPTFLOW_SKILL_HOME', 'User')
$priorProfile = [Environment]::GetEnvironmentVariable('PPTFLOW_CHATGPT_PROFILE', 'User')
$priorProcessSkillHome = $env:PPTFLOW_SKILL_HOME
$priorProcessProfile = $env:PPTFLOW_CHATGPT_PROFILE

function Move-Recoverably {
    param([string]$Source, [string]$Destination)

    New-Item -ItemType Directory -Path (Split-Path $Destination -Parent) -Force | Out-Null
    Move-Item -LiteralPath $Source -Destination $Destination
    $moves.Add([pscustomobject]@{ Source = $Source; Destination = $Destination })
}

try {
    foreach ($name in $skillNames) {
        Move-Recoverably -Source (Join-Path $DestinationRoot $name) -Destination (Join-Path $backupRoot "skills\$name")
    }
    if ($wrapperOwned) {
        Move-Recoverably -Source $wrapperPath -Destination (Join-Path $backupRoot 'bin\pptflow.cmd')
    }
    Move-Recoverably -Source $manifestPath -Destination (Join-Path $backupRoot 'state\install-manifest.json')

    if ($priorSkillHome -and $priorSkillHome -ieq [string]$manifest.pptflow_skill_home) {
        [Environment]::SetEnvironmentVariable('PPTFLOW_SKILL_HOME', $null, 'User')
    }
    if ($priorProfile -and $priorProfile -ieq [string]$manifest.pptflow_chatgpt_profile) {
        [Environment]::SetEnvironmentVariable('PPTFLOW_CHATGPT_PROFILE', $null, 'User')
    }
    if ($env:PPTFLOW_SKILL_HOME -and $env:PPTFLOW_SKILL_HOME -ieq [string]$manifest.pptflow_skill_home) {
        Remove-Item Env:PPTFLOW_SKILL_HOME
    }
    if ($env:PPTFLOW_CHATGPT_PROFILE -and $env:PPTFLOW_CHATGPT_PROFILE -ieq [string]$manifest.pptflow_chatgpt_profile) {
        Remove-Item Env:PPTFLOW_CHATGPT_PROFILE
    }

    $wrapperStatus = if ($wrapperOwned) {
        'moved to backup'
    }
    elseif ($wrapperPath) {
        'retained because it was missing or modified'
    }
    else {
        'not recorded'
    }

    [ordered]@{
        uninstalled = $true
        moved = @($moves | ForEach-Object { $_.Source })
        backup = $backupRoot
        wrapper = $wrapperStatus
        retained = @(
            [string]$manifest.pptflow_chatgpt_profile,
            (Join-Path $env:USERPROFILE '.editppt'),
            'the editppt tool installation'
        )
    } | ConvertTo-Json -Depth 4
}
catch {
    $originalError = $_.Exception.Message
    $rollbackErrors = [System.Collections.Generic.List[string]]::new()
    function Invoke-RollbackAction {
        param(
            [string]$Description,
            [scriptblock]$Action
        )

        try {
            & $Action
        }
        catch {
            $rollbackErrors.Add("${Description}: $($_.Exception.Message)")
        }
    }

    for ($index = $moves.Count - 1; $index -ge 0; $index--) {
        $move = $moves[$index]
        $sourceExists = Test-Path -LiteralPath $move.Source
        $destinationExists = Test-Path -LiteralPath $move.Destination
        if ($destinationExists -and -not $sourceExists) {
            Invoke-RollbackAction -Description "restore $($move.Source)" -Action {
                New-Item -ItemType Directory -Path (Split-Path $move.Source -Parent) -Force | Out-Null
                Move-Item -LiteralPath $move.Destination -Destination $move.Source
            }
        }
        elseif ($destinationExists -and $sourceExists) {
            $rollbackErrors.Add("restore $($move.Source): source is occupied while backup still exists")
        }
        elseif (-not $destinationExists -and -not $sourceExists) {
            $rollbackErrors.Add("restore $($move.Source): both source and backup are missing")
        }
    }
    Invoke-RollbackAction -Description 'restore user PPTFLOW_SKILL_HOME' -Action {
        [Environment]::SetEnvironmentVariable('PPTFLOW_SKILL_HOME', $priorSkillHome, 'User')
    }
    Invoke-RollbackAction -Description 'restore user PPTFLOW_CHATGPT_PROFILE' -Action {
        [Environment]::SetEnvironmentVariable('PPTFLOW_CHATGPT_PROFILE', $priorProfile, 'User')
    }
    Invoke-RollbackAction -Description 'restore current process environment' -Action {
        if ($null -eq $priorProcessSkillHome) {
            Remove-Item Env:PPTFLOW_SKILL_HOME -ErrorAction SilentlyContinue
        }
        else {
            $env:PPTFLOW_SKILL_HOME = $priorProcessSkillHome
        }
        if ($null -eq $priorProcessProfile) {
            Remove-Item Env:PPTFLOW_CHATGPT_PROFILE -ErrorAction SilentlyContinue
        }
        else {
            $env:PPTFLOW_CHATGPT_PROFILE = $priorProcessProfile
        }
    }

    if ($rollbackErrors.Count -eq 0) {
        throw "Uninstall failed. Rollback completed. Original error: $originalError"
    }
    throw "Uninstall failed. Rollback was partial; inspect $backupRoot before retrying. Original error: $originalError Rollback errors: $($rollbackErrors -join ' | ')"
}
