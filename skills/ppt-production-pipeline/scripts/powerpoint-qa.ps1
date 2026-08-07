[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Pptx,

    [Parameter(Mandatory = $true)]
    [string]$QaDir
)

$ErrorActionPreference = 'Stop'
$pptxPath = (Resolve-Path -LiteralPath $Pptx).Path
if ([IO.Path]::GetExtension($pptxPath) -ine '.pptx') {
    throw "Expected a .pptx file: $pptxPath"
}

$qaPath = [IO.Path]::GetFullPath($QaDir)
$renderPath = Join-Path $qaPath 'powerpoint-render'
New-Item -ItemType Directory -Path $renderPath -Force | Out-Null
$reportPath = Join-Path $qaPath 'powerpoint-qa.json'

$powerPoint = $null
$presentation = $null
$exports = @()
$report = [ordered]@{
    schema_version = '1.0'
    passed = $false
    checked_at = [DateTime]::UtcNow.ToString('o')
    pptx_path = $pptxPath
    pptx_sha256 = (Get-FileHash -LiteralPath $pptxPath -Algorithm SHA256).Hash.ToLowerInvariant()
    slide_count = 0
    exports = @()
    error = $null
}

try {
    $powerPoint = New-Object -ComObject PowerPoint.Application
    $presentation = $powerPoint.Presentations.Open($pptxPath, $true, $false, $false)
    $report.slide_count = [int]$presentation.Slides.Count
    for ($index = 1; $index -le $presentation.Slides.Count; $index++) {
        $name = ('slide-{0:D3}.png' -f $index)
        $target = Join-Path $renderPath $name
        $presentation.Slides.Item($index).Export($target, 'PNG', 1600, 900)
        if (-not (Test-Path -LiteralPath $target -PathType Leaf) -or (Get-Item -LiteralPath $target).Length -eq 0) {
            throw "PowerPoint did not export slide $index"
        }
        $exports += ('powerpoint-render/' + $name)
    }
    $report.exports = $exports
    $report.passed = ($exports.Count -eq $report.slide_count -and $report.slide_count -gt 0)
}
catch {
    $report.error = $_.Exception.Message
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

$temporary = Join-Path $qaPath ('.powerpoint-qa.' + [Guid]::NewGuid().ToString('N') + '.tmp')
[IO.File]::WriteAllText($temporary, ($report | ConvertTo-Json -Depth 6), [Text.UTF8Encoding]::new($false))
Move-Item -LiteralPath $temporary -Destination $reportPath -Force

$report | ConvertTo-Json -Depth 6
if (-not $report.passed) {
    exit 1
}
