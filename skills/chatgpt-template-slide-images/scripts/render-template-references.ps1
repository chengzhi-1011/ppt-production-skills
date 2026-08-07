[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Run,

    [int]$Width = 1600,
    [int]$Height = 900
)

$ErrorActionPreference = 'Stop'
$runPath = (Resolve-Path -LiteralPath $Run).Path
$manifestPath = Join-Path $runPath 'input\input-manifest.json'
if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
    throw "Missing input manifest: $manifestPath"
}

$manifest = Get-Content -Raw -LiteralPath $manifestPath | ConvertFrom-Json
$templateRelative = [string]$manifest.template.path
if ([string]::IsNullOrWhiteSpace($templateRelative)) {
    throw 'The input manifest does not contain template.path.'
}

$templatePath = [IO.Path]::GetFullPath((Join-Path $runPath $templateRelative))
$runPrefix = $runPath.TrimEnd('\') + '\'
if (-not $templatePath.StartsWith($runPrefix, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Template path escapes the run directory: $templateRelative"
}
if (-not (Test-Path -LiteralPath $templatePath -PathType Leaf)) {
    throw "Template snapshot is missing: $templatePath"
}

$outputPath = Join-Path $runPath 'generation\template-references'
New-Item -ItemType Directory -Path $outputPath -Force | Out-Null
$references = @()
$powerPoint = $null
$presentation = $null

try {
    $extension = [IO.Path]::GetExtension($templatePath).ToLowerInvariant()
    if ($extension -eq '.pptx') {
        $powerPoint = New-Object -ComObject PowerPoint.Application
        $presentation = $powerPoint.Presentations.Open($templatePath, $true, $false, $false)
        for ($index = 1; $index -le $presentation.Slides.Count; $index++) {
            $name = ('STYLE_REF_{0:D3}.png' -f $index)
            $target = Join-Path $outputPath $name
            $presentation.Slides.Item($index).Export($target, 'PNG', $Width, $Height)
            if (-not (Test-Path -LiteralPath $target -PathType Leaf) -or (Get-Item -LiteralPath $target).Length -eq 0) {
                throw "PowerPoint did not export template slide $index."
            }
            $references += [ordered]@{
                slide_number = $index
                path = ('generation/template-references/' + $name)
                sha256 = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant()
                width = $Width
                height = $Height
            }
        }
    }
    elseif ($extension -in @('.png', '.jpg', '.jpeg')) {
        $name = 'STYLE_REF_001' + $extension
        $target = Join-Path $outputPath $name
        Copy-Item -LiteralPath $templatePath -Destination $target -Force
        $references += [ordered]@{
            slide_number = 1
            path = ('generation/template-references/' + $name)
            sha256 = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant()
            width = $null
            height = $null
        }
    }
    else {
        throw "Unsupported template type '$extension'. Supply PPTX, PNG, JPG, or JPEG. Macro-enabled PPTM files are intentionally rejected."
    }
}
finally {
    if ($presentation) {
        try { $presentation.Close() } catch { }
        [void][Runtime.InteropServices.Marshal]::ReleaseComObject($presentation)
    }
    if ($powerPoint) {
        try { $powerPoint.Quit() } catch { }
        [void][Runtime.InteropServices.Marshal]::ReleaseComObject($powerPoint)
    }
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
}

if ($references.Count -eq 0) {
    throw 'No template references were produced.'
}

$report = [ordered]@{
    schema_version = '1.0'
    created_at = [DateTime]::UtcNow.ToString('o')
    template_path = $templateRelative.Replace('\', '/')
    template_sha256 = (Get-FileHash -LiteralPath $templatePath -Algorithm SHA256).Hash.ToLowerInvariant()
    reference_count = $references.Count
    references = $references
}
$reportPath = Join-Path $runPath 'generation\template-reference-manifest.json'
$temporary = Join-Path $runPath ('generation\.template-reference-manifest.' + [Guid]::NewGuid().ToString('N') + '.tmp')
[IO.File]::WriteAllText($temporary, ($report | ConvertTo-Json -Depth 6), [Text.UTF8Encoding]::new($false))
Move-Item -LiteralPath $temporary -Destination $reportPath -Force
$report | ConvertTo-Json -Depth 6
