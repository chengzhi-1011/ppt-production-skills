[CmdletBinding()]
param(
    [string]$DestinationRoot = (Join-Path $env:USERPROFILE '.codex\skills'),
    [switch]$SkipEditpptInstall,
    [switch]$ForceReplace,
    [switch]$ReplaceEditppt
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$sourceRoot = Join-Path $repoRoot 'skills'
$skillNames = @(
    'ppt-production-pipeline',
    'ppt-material-to-outline',
    'chatgpt-template-slide-images',
    'image-to-editable-ppt'
)

function Test-PathOverlap {
    param([string]$Left, [string]$Right)

    $leftFull = [IO.Path]::GetFullPath($Left).TrimEnd('\')
    $rightFull = [IO.Path]::GetFullPath($Right).TrimEnd('\')
    return $leftFull.Equals($rightFull, [StringComparison]::OrdinalIgnoreCase) -or
        $leftFull.StartsWith($rightFull + '\', [StringComparison]::OrdinalIgnoreCase) -or
        $rightFull.StartsWith($leftFull + '\', [StringComparison]::OrdinalIgnoreCase)
}

function Copy-SkillTree {
    param([string]$Source, [string]$Destination)

    New-Item -ItemType Directory -Path $Destination -Force | Out-Null
    Get-ChildItem -LiteralPath $Source -Force -Recurse |
        Where-Object {
            $_.FullName -notmatch '[\\/](?:__pycache__|\.venv|dist(?:-[^\\/]+)?|build|\.pytest_cache)(?:[\\/]|$)' -and
            $_.FullName -notmatch '[\\/][^\\/]+\.egg-info(?:[\\/]|$)' -and
            $_.Extension -ne '.pyc'
        } |
        ForEach-Object {
            $relative = $_.FullName.Substring($Source.Length).TrimStart('\')
            $target = Join-Path $Destination $relative
            if ($_.PSIsContainer) {
                New-Item -ItemType Directory -Path $target -Force | Out-Null
            }
            else {
                New-Item -ItemType Directory -Path (Split-Path $target -Parent) -Force | Out-Null
                Copy-Item -LiteralPath $_.FullName -Destination $target -Force
            }
        }
}

# Complete every non-mutating preflight before replacing an installed Skill.
$DestinationRoot = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($DestinationRoot)
$repoRoot = [IO.Path]::GetFullPath($repoRoot)
$sourceRoot = [IO.Path]::GetFullPath($sourceRoot)
$destinationDriveRoot = [IO.Path]::GetPathRoot($DestinationRoot).TrimEnd('\')
if ($DestinationRoot.TrimEnd('\').Equals($destinationDriveRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'DestinationRoot must not be a drive root.'
}
if (Test-PathOverlap -Left $DestinationRoot -Right $repoRoot) {
    throw 'DestinationRoot must not overlap the repository source tree.'
}
if (Test-Path -LiteralPath $DestinationRoot) {
    $destinationItem = Get-Item -LiteralPath $DestinationRoot -Force
    if (-not $destinationItem.PSIsContainer -or ($destinationItem.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw 'DestinationRoot must be a normal directory, not a file or reparse point.'
    }
}
foreach ($name in $skillNames) {
    $source = Join-Path $sourceRoot $name
    if (-not (Test-Path -LiteralPath (Join-Path $source 'SKILL.md') -PathType Leaf)) {
        throw "Missing skill source: $source"
    }
}
$pipelineSource = Join-Path $sourceRoot 'ppt-production-pipeline'
foreach ($requiredSource in @(
    (Join-Path $pipelineSource 'scripts\install.ps1'),
    (Join-Path $pipelineSource 'scripts\pptflow.py')
)) {
    if (-not (Test-Path -LiteralPath $requiredSource -PathType Leaf)) {
        throw "Missing pipeline source: $requiredSource"
    }
}
if (-not (Get-Command agent-browser -ErrorAction SilentlyContinue)) {
    throw 'agent-browser is missing. Install it with: npm install -g agent-browser'
}
if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
    throw 'The Windows py launcher is required by pptflow.cmd.'
}
& py -3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)'
$pythonExitCode = $LASTEXITCODE
if ($pythonExitCode -ne 0) {
    throw 'Python 3.10 or newer must be available through `py -3`.'
}
if (-not $SkipEditpptInstall -and -not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw 'uv is required for an isolated editppt tool installation.'
}
if ($SkipEditpptInstall -and -not (Get-Command editppt -ErrorAction SilentlyContinue)) {
    throw '-SkipEditpptInstall requires an existing editppt command on PATH.'
}

$manifestPath = Join-Path $DestinationRoot '.ppt-production-skills-install.json'
$existingManifest = $null
if (Test-Path -LiteralPath $manifestPath -PathType Leaf) {
    try {
        $existingManifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    }
    catch {
        if (-not $ForceReplace) {
            throw 'Existing installation manifest is unreadable; use -ForceReplace only after reviewing the destination.'
        }
    }
}
foreach ($name in $skillNames) {
    $destination = Join-Path $DestinationRoot $name
    if (-not (Test-Path -LiteralPath $destination)) {
        continue
    }
    $item = Get-Item -LiteralPath $destination -Force
    if (-not $item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw "Refusing to replace a non-directory or reparse-point target: $destination"
    }
    $ownerPath = Join-Path $destination '.ppt-production-skills-owner.json'
    $owned = $false
    if (Test-Path -LiteralPath $ownerPath -PathType Leaf) {
        try {
            $owner = Get-Content -LiteralPath $ownerPath -Raw -Encoding UTF8 | ConvertFrom-Json
            $owned = $owner.package -eq 'chengzhi-1011/ppt-production-skills' -and $owner.skill -eq $name
        }
        catch {
            $owned = $false
        }
    }
    if (-not $owned -and -not $ForceReplace) {
        throw "Existing Skill is not owned by this installer: $destination. Review it, then rerun with -ForceReplace to back it up and replace it."
    }
}

$wrapperPath = Join-Path $env:USERPROFILE '.local\bin\pptflow.cmd'
if ((Test-Path -LiteralPath $wrapperPath -PathType Leaf) -and -not $ForceReplace) {
    $wrapperOwned = $false
    if ($existingManifest -and $existingManifest.wrapper_sha256) {
        $wrapperOwned = (Get-FileHash -LiteralPath $wrapperPath -Algorithm SHA256).Hash.ToLowerInvariant() -eq [string]$existingManifest.wrapper_sha256
    }
    if (-not $wrapperOwned) {
        throw "Existing pptflow.cmd is not owned by this installer: $wrapperPath. Review it, then rerun with -ForceReplace."
    }
}
$existingEditppt = Get-Command editppt -ErrorAction SilentlyContinue
$uvToolAlreadyInstalled = $false
$uvEditpptPath = $null
if (-not $SkipEditpptInstall) {
    $priorErrorActionPreference = $ErrorActionPreference
    try {
        # Windows PowerShell 5.1 turns native stderr into ErrorRecord objects.
        # `uv tool list` writes "No tools installed" to stderr with exit code 0.
        $ErrorActionPreference = 'Continue'
        $uvToolList = @(& uv tool list --show-paths 2>$null)
        $uvToolListExitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $priorErrorActionPreference
    }
    if ($uvToolListExitCode -ne 0) {
        throw "Unable to inspect existing uv tools (exit code $uvToolListExitCode)."
    }
    for ($lineIndex = 0; $lineIndex -lt $uvToolList.Count; $lineIndex++) {
        if ([string]$uvToolList[$lineIndex] -notmatch '^image-to-editable-ppt-cli\s+v') {
            continue
        }
        $uvToolAlreadyInstalled = $true
        for ($entryIndex = $lineIndex + 1; $entryIndex -lt $uvToolList.Count; $entryIndex++) {
            $entry = [string]$uvToolList[$entryIndex]
            if ($entry -notmatch '^\s*-') {
                break
            }
            if ($entry -match '^\s*-\s+editppt\s+\((?<path>.+)\)\s*$') {
                $uvEditpptPath = $Matches.path
                break
            }
        }
        break
    }
}
if (-not $SkipEditpptInstall -and $existingEditppt) {
    $knownUvTool = $uvToolAlreadyInstalled -and $uvEditpptPath
    if ($knownUvTool) {
        try {
            $knownUvTool = [IO.Path]::GetFullPath([string]$existingEditppt.Source).Equals(
                [IO.Path]::GetFullPath([string]$uvEditpptPath),
                [StringComparison]::OrdinalIgnoreCase
            )
        }
        catch {
            $knownUvTool = $false
        }
    }
    if (-not $knownUvTool) {
        throw "The existing editppt command is not the uv-managed image-to-editable-ppt-cli tool and will not be overwritten: $($existingEditppt.Source). Use -SkipEditpptInstall to keep it or remove it manually."
    }
    if (-not $ReplaceEditppt) {
        throw "The uv-managed editppt tool already exists at $($existingEditppt.Source). Rerun with -ReplaceEditppt to upgrade it, or -SkipEditpptInstall to keep it."
    }
}
elseif (-not $SkipEditpptInstall -and $uvToolAlreadyInstalled -and -not $ReplaceEditppt) {
    throw 'The uv-managed image-to-editable-ppt-cli tool is installed but editppt is not on PATH. Rerun with -ReplaceEditppt to repair it, or fix PATH and use -SkipEditpptInstall.'
}

$stamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
$installId = [guid]::NewGuid().ToString('N')
$backupRoot = Join-Path $env:USERPROFILE ".ppt-production-skills-backup\install-$stamp-$installId"
$failedRoot = Join-Path $backupRoot 'failed-new'
$installedDestinations = [System.Collections.Generic.List[string]]::new()
$previousSkills = @{}
$priorUserPath = [Environment]::GetEnvironmentVariable('Path', 'User')
$priorSkillHome = [Environment]::GetEnvironmentVariable('PPTFLOW_SKILL_HOME', 'User')
$priorProfile = [Environment]::GetEnvironmentVariable('PPTFLOW_CHATGPT_PROFILE', 'User')
$priorProcessPath = $env:Path
$priorProcessSkillHome = $env:PPTFLOW_SKILL_HOME
$priorProcessProfile = $env:PPTFLOW_CHATGPT_PROFILE
$newEditpptInstallAttempted = $false
$existingEditpptReplaced = $false
$wrapperBackup = Join-Path $backupRoot 'state\pptflow.cmd'
$hadWrapper = Test-Path -LiteralPath $wrapperPath -PathType Leaf
$manifestBackup = Join-Path $backupRoot 'state\install-manifest.json'
$hadManifest = Test-Path -LiteralPath $manifestPath -PathType Leaf

try {
    New-Item -ItemType Directory -Path $DestinationRoot -Force | Out-Null
    if ($hadManifest) {
        New-Item -ItemType Directory -Path (Split-Path $manifestBackup -Parent) -Force | Out-Null
        Move-Item -LiteralPath $manifestPath -Destination $manifestBackup
    }
    foreach ($name in $skillNames) {
        $source = Join-Path $sourceRoot $name
        $destination = Join-Path $DestinationRoot $name
        $backup = Join-Path $backupRoot "skills\$name"
        if (Test-Path -LiteralPath $destination) {
            New-Item -ItemType Directory -Path (Split-Path $backup -Parent) -Force | Out-Null
            Move-Item -LiteralPath $destination -Destination $backup
            $previousSkills[$name] = $backup
        }
        $installedDestinations.Add($destination)
        Copy-SkillTree -Source $source -Destination $destination
        $owner = [ordered]@{
            schema_version = 1
            package = 'chengzhi-1011/ppt-production-skills'
            installation_id = $installId
            skill = $name
        } | ConvertTo-Json -Depth 3
        [IO.File]::WriteAllText(
            (Join-Path $destination '.ppt-production-skills-owner.json'),
            $owner + [Environment]::NewLine,
            [Text.UTF8Encoding]::new($false)
        )
    }

    if ($hadWrapper) {
        New-Item -ItemType Directory -Path (Split-Path $wrapperBackup -Parent) -Force | Out-Null
        Copy-Item -LiteralPath $wrapperPath -Destination $wrapperBackup
    }

    $editpptCli = Join-Path $DestinationRoot 'image-to-editable-ppt\cli'
    if (-not $SkipEditpptInstall) {
        $toolArguments = @('tool', 'install')
        if ($uvToolAlreadyInstalled) {
            $toolArguments += '--force'
        }
        $toolArguments += $editpptCli
        $newEditpptInstallAttempted = -not $uvToolAlreadyInstalled
        & uv @toolArguments
        $toolExitCode = $LASTEXITCODE
        if ($toolExitCode -ne 0) {
            throw "editppt installation failed with exit code $toolExitCode"
        }
        $existingEditpptReplaced = $uvToolAlreadyInstalled
    }

    $pipelineHome = Join-Path $DestinationRoot 'ppt-production-pipeline'
    $pipelineInstaller = Join-Path $pipelineHome 'scripts\install.ps1'
    & $pipelineInstaller -SkillHome $pipelineHome

    $manifest = [ordered]@{
        schema_version = 1
        package = 'chengzhi-1011/ppt-production-skills'
        installation_id = $installId
        installed_at = (Get-Date).ToUniversalTime().ToString('o')
        destination_root = $DestinationRoot
        skills = $skillNames
        wrapper_path = $wrapperPath
        wrapper_sha256 = (Get-FileHash -LiteralPath $wrapperPath -Algorithm SHA256).Hash.ToLowerInvariant()
        pptflow_skill_home = $pipelineHome
        pptflow_chatgpt_profile = [Environment]::GetEnvironmentVariable('PPTFLOW_CHATGPT_PROFILE', 'User')
    } | ConvertTo-Json -Depth 5
    [IO.File]::WriteAllText($manifestPath, $manifest + [Environment]::NewLine, [Text.UTF8Encoding]::new($false))

    [ordered]@{
        installed = $true
        destination_root = (Resolve-Path -LiteralPath $DestinationRoot).Path
        skills = $skillNames
        editppt = (Get-Command editppt -ErrorAction SilentlyContinue).Source
        pptflow = (Get-Command pptflow -ErrorAction SilentlyContinue).Source
        previous_install_backup = $backupRoot
        install_manifest = $manifestPath
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

    Invoke-RollbackAction -Description 'create failed-files directory' -Action {
        New-Item -ItemType Directory -Path $failedRoot -Force | Out-Null
    }
    if ($newEditpptInstallAttempted) {
        Invoke-RollbackAction -Description 'remove editppt installation attempted by this run' -Action {
            & uv tool uninstall image-to-editable-ppt-cli
            $uninstallToolExitCode = $LASTEXITCODE
            $ErrorActionPreference = 'Continue'
            $remainingTools = @(& uv tool list 2>$null)
            $toolListExitCode = $LASTEXITCODE
            $ErrorActionPreference = 'Stop'
            if ($toolListExitCode -ne 0) {
                throw "uv tool uninstall returned $uninstallToolExitCode and verification failed with exit code $toolListExitCode"
            }
            if ($remainingTools | Where-Object { [string]$_ -match '^image-to-editable-ppt-cli\s+v' }) {
                throw "uv tool uninstall returned $uninstallToolExitCode and the tool is still installed"
            }
        }
    }
    if ($existingEditpptReplaced) {
        $rollbackErrors.Add(
            'restore previous editppt: the explicitly replaced uv tool remains installed; reinstall the prior version manually if required'
        )
    }
    if (Test-Path -LiteralPath $manifestPath -PathType Leaf) {
        Invoke-RollbackAction -Description 'preserve failed install manifest' -Action {
            Move-Item -LiteralPath $manifestPath -Destination (Join-Path $failedRoot 'install-manifest.json')
        }
    }
    for ($index = $installedDestinations.Count - 1; $index -ge 0; $index--) {
        $destination = $installedDestinations[$index]
        $name = Split-Path $destination -Leaf
        if (Test-Path -LiteralPath $destination) {
            $failedDestination = Join-Path $failedRoot $name
            Invoke-RollbackAction -Description "preserve failed Skill $name" -Action {
                Move-Item -LiteralPath $destination -Destination $failedDestination
            }
        }
        if ($previousSkills.ContainsKey($name)) {
            $previousSkill = $previousSkills[$name]
            if (Test-Path -LiteralPath $previousSkill) {
                Invoke-RollbackAction -Description "restore previous Skill $name" -Action {
                    if (Test-Path -LiteralPath $destination) {
                        throw "destination is still occupied: $destination"
                    }
                    Move-Item -LiteralPath $previousSkill -Destination $destination
                }
            }
            else {
                $rollbackErrors.Add("restore previous Skill ${name}: backup is missing at $previousSkill")
            }
        }
    }
    Invoke-RollbackAction -Description 'restore user PATH' -Action {
        [Environment]::SetEnvironmentVariable('Path', $priorUserPath, 'User')
    }
    Invoke-RollbackAction -Description 'restore user PPTFLOW_SKILL_HOME' -Action {
        [Environment]::SetEnvironmentVariable('PPTFLOW_SKILL_HOME', $priorSkillHome, 'User')
    }
    Invoke-RollbackAction -Description 'restore user PPTFLOW_CHATGPT_PROFILE' -Action {
        [Environment]::SetEnvironmentVariable('PPTFLOW_CHATGPT_PROFILE', $priorProfile, 'User')
    }
    Invoke-RollbackAction -Description 'restore current process environment' -Action {
        $env:Path = $priorProcessPath
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
    if ($hadWrapper) {
        if (Test-Path -LiteralPath $wrapperBackup -PathType Leaf) {
            Invoke-RollbackAction -Description 'restore previous pptflow wrapper' -Action {
                New-Item -ItemType Directory -Path (Split-Path $wrapperPath -Parent) -Force | Out-Null
                Copy-Item -LiteralPath $wrapperBackup -Destination $wrapperPath -Force
            }
        }
        else {
            $rollbackErrors.Add("restore previous pptflow wrapper: backup is missing at $wrapperBackup")
        }
    }
    elseif (-not $hadWrapper -and (Test-Path -LiteralPath $wrapperPath -PathType Leaf)) {
        Invoke-RollbackAction -Description 'preserve failed pptflow wrapper' -Action {
            Move-Item -LiteralPath $wrapperPath -Destination (Join-Path $failedRoot 'pptflow.cmd')
        }
    }
    if ($hadManifest) {
        if (Test-Path -LiteralPath $manifestBackup -PathType Leaf) {
            Invoke-RollbackAction -Description 'restore previous install manifest' -Action {
                if (Test-Path -LiteralPath $manifestPath) {
                    throw "manifest destination is still occupied: $manifestPath"
                }
                Move-Item -LiteralPath $manifestBackup -Destination $manifestPath
            }
        }
        else {
            $rollbackErrors.Add("restore previous install manifest: backup is missing at $manifestBackup")
        }
    }

    if ($rollbackErrors.Count -eq 0) {
        throw "Installation failed. Rollback completed. Failed files are in $failedRoot. Original error: $originalError"
    }
    throw "Installation failed. Rollback was partial; inspect $backupRoot before retrying. Original error: $originalError Rollback errors: $($rollbackErrors -join ' | ')"
}
