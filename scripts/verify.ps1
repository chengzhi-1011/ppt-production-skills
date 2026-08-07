[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$env:PYTHONDONTWRITEBYTECODE = '1'
$repoRoot = Split-Path -Parent $PSScriptRoot
$skillRoot = Join-Path $repoRoot 'skills'
$excludedSourcePattern = '[\\/](?:\.git|\.venv|dist(?:-[^\\/]+)?|build|__pycache__|\.pytest_cache)(?:[\\/]|$)|[\\/][^\\/]+\.egg-info(?:[\\/]|$)'
$skillNames = @(
    'ppt-production-pipeline',
    'ppt-material-to-outline',
    'chatgpt-template-slide-images',
    'image-to-editable-ppt'
)

foreach ($name in $skillNames) {
    $skillFile = Join-Path $skillRoot "$name\SKILL.md"
    if (-not (Test-Path -LiteralPath $skillFile -PathType Leaf)) {
        throw "Missing $skillFile"
    }
}

$forbidden = Get-ChildItem -LiteralPath $repoRoot -File -Recurse -Force |
    Where-Object {
        $_.FullName -notmatch '[\\/](?:\.git|\.venv)(?:[\\/]|$)' -and
        ($_.FullName -match '[\\/]__pycache__([\\/]|$)' -or $_.Extension -eq '.pyc')
    }
if ($forbidden) {
    throw "Generated Python cache files are present: $($forbidden.FullName -join ', ')"
}

$pythonFiles = Get-ChildItem -LiteralPath $repoRoot -Filter '*.py' -File -Recurse |
    Where-Object { $_.FullName -notmatch $excludedSourcePattern }
foreach ($file in $pythonFiles) {
    $env:PPT_SKILLS_VERIFY_TARGET = $file.FullName
    @'
import os
from pathlib import Path
p = Path(os.environ["PPT_SKILLS_VERIFY_TARGET"])
compile(p.read_text(encoding="utf-8-sig"), str(p), "exec")
'@ | py -B -X utf8 -
    if ($LASTEXITCODE -ne 0) {
        throw "Python syntax validation failed: $($file.FullName)"
    }
}
Remove-Item Env:PPT_SKILLS_VERIFY_TARGET -ErrorAction SilentlyContinue

$powerShellFiles = Get-ChildItem -LiteralPath $repoRoot -Filter '*.ps1' -File -Recurse |
    Where-Object { $_.FullName -notmatch $excludedSourcePattern }
foreach ($file in $powerShellFiles) {
    $tokens = $null
    $errors = $null
    [System.Management.Automation.Language.Parser]::ParseFile(
        $file.FullName,
        [ref]$tokens,
        [ref]$errors
    ) | Out-Null
    if ($errors) {
        throw "PowerShell syntax validation failed: $($file.FullName): $($errors.Message -join '; ')"
    }
}

$quickValidate = Join-Path $env:USERPROFILE '.codex\skills\.system\skill-creator\scripts\quick_validate.py'
$validatedSkills = 0
if (Test-Path -LiteralPath $quickValidate -PathType Leaf) {
    foreach ($name in $skillNames) {
        & py -B -X utf8 $quickValidate (Join-Path $skillRoot $name)
        if ($LASTEXITCODE -ne 0) {
            throw "Skill validation failed: $name"
        }
        $validatedSkills += 1
    }
}

$trackedPaths = @()
if (Test-Path -LiteralPath (Join-Path $repoRoot '.git')) {
    $trackedPaths = @(& git -C $repoRoot ls-files)
    if ($LASTEXITCODE -ne 0) {
        throw 'git ls-files failed.'
    }
}
else {
    $trackedPaths = @(Get-ChildItem -LiteralPath $repoRoot -File -Recurse -Force |
        Where-Object { $_.FullName -notmatch '[\\/](?:\.git|\.venv)(?:[\\/]|$)' } |
        ForEach-Object { $_.FullName.Substring($repoRoot.Length + 1) })
}

$forbiddenAssetExtensions = @('.png', '.jpg', '.jpeg', '.gif', '.webp', '.pdf', '.ppt', '.pptx', '.pptm', '.doc', '.docx', '.xls', '.xlsx')
$trackedAssets = @($trackedPaths | Where-Object { $forbiddenAssetExtensions -contains [IO.Path]::GetExtension($_).ToLowerInvariant() })
if ($trackedAssets) {
    throw "Task/customer or generated binary assets are tracked: $($trackedAssets -join ', ')"
}

$forbiddenTrackedNames = @('.env', 'auth.json', 'cookies.json', 'storage-state.json')
$trackedSecrets = @($trackedPaths | Where-Object { $forbiddenTrackedNames -contains [IO.Path]::GetFileName($_).ToLowerInvariant() })
if ($trackedSecrets) {
    throw "Credential-bearing filenames are tracked: $($trackedSecrets -join ', ')"
}

$textExtensions = @('.py', '.ps1', '.md', '.toml', '.yaml', '.yml', '.json', '.txt', '.cmd')
$trackedContent = foreach ($relative in $trackedPaths) {
    if ($textExtensions -contains [IO.Path]::GetExtension($relative).ToLowerInvariant()) {
        $full = Join-Path $repoRoot ($relative -replace '/', '\')
        if ((Test-Path -LiteralPath $full -PathType Leaf) -and (Get-Item -LiteralPath $full).Length -lt 5MB) {
            Get-Item -LiteralPath $full
        }
    }
}

$privateBackendMarkers = @(
    ('chatgpt.com/' + 'backend-api'),
    ('.codex/' + 'auth.json'),
    ('CODEX_' + 'AUTH_FILE'),
    ('CODEX_' + 'IMAGES_BASE_URL'),
    ('ChatGPT-' + 'Account-ID'),
    ('codex-' + 'oauth'),
    ('codex ' + 'login'),
    ('--' + 'token')
)
foreach ($marker in $privateBackendMarkers) {
    $hits = $trackedContent | Select-String -SimpleMatch $marker -ErrorAction SilentlyContinue
    if ($hits) {
        throw "Private backend marker found: $marker in $($hits.Path | Select-Object -Unique)"
    }
}

$secretPatterns = @(
    '-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',
    '(?<![A-Za-z0-9])(?:sk|rk)-[A-Za-z0-9_-]{20,}',
    'github_pat_[A-Za-z0-9_]{20,}',
    'gh[pousr]_[A-Za-z0-9]{20,}'
)
foreach ($pattern in $secretPatterns) {
    $hits = $trackedContent | Select-String -Pattern $pattern -ErrorAction SilentlyContinue
    if ($hits) {
        throw "Potential secret found by pattern $pattern in $($hits.Path | Select-Object -Unique)"
    }
}

$hex40Pattern = '(?<![A-Fa-f0-9])[A-Fa-f0-9]{40}(?![A-Fa-f0-9])'
$pinnedActionPattern = '^\s*(?:-\s+)?uses:\s*[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*@[A-Fa-f0-9]{40}\s*(?:#.*)?$'
foreach ($file in $trackedContent) {
    $hits = Select-String -LiteralPath $file.FullName -Pattern $hex40Pattern -ErrorAction SilentlyContinue
    foreach ($hit in $hits) {
        if ($hit.Line -match $pinnedActionPattern) {
            continue
        }
        throw "Potential 40-character secret found in $($file.FullName):$($hit.LineNumber)"
    }
}

$absoluteUserPathHits = $trackedContent | Select-String -Pattern '(?i)[A-Z]:\\Users\\[^\\\r\n]+' -ErrorAction SilentlyContinue
if ($absoluteUserPathHits) {
    throw "Hard-coded local user path found: $($absoluteUserPathHits.Path | Select-Object -Unique)"
}

$cliRoot = Join-Path $skillRoot 'image-to-editable-ppt\cli'
if (Get-Command uv -ErrorAction SilentlyContinue) {
    & uv run --project $cliRoot --locked python -B -m unittest discover -s (Join-Path $cliRoot 'tests') -v
}
else {
    & py -B -X utf8 -m unittest discover -s (Join-Path $cliRoot 'tests') -v
}
if ($LASTEXITCODE -ne 0) {
    throw 'Unit tests failed.'
}

if (Test-Path -LiteralPath (Join-Path $repoRoot '.git')) {
    & git -C $repoRoot diff --check
    if ($LASTEXITCODE -ne 0) {
        throw 'git diff --check failed.'
    }
}

[ordered]@{
    passed = $true
    skills = $skillNames.Count
    skill_creator_validations = $validatedSkills
    python_files = $pythonFiles.Count
    powershell_files = $powerShellFiles.Count
    generated_cache_files = 0
    hardcoded_local_paths = 0
    tracked_binary_assets = 0
    potential_secrets = 0
    unit_tests = 'passed'
} | ConvertTo-Json -Depth 3
