param(
    [string]$GpuDir = '.artifacts/easyocr-det-gpu-177-v1',
    [string]$CpuDir = '.artifacts/easyocr-det-177-v1'
)
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
function Get-Stats($Values) {
    $sorted = @($Values | Sort-Object)
    if (-not $sorted.Count) { throw 'No timing samples' }
    $median = if ($sorted.Count % 2) { $sorted[[int][math]::Floor($sorted.Count / 2)] } else {
        ($sorted[$sorted.Count / 2 - 1] + $sorted[$sorted.Count / 2]) / 2
    }
    [ordered]@{ n=$sorted.Count; mean_s=($sorted | Measure-Object -Average).Average; median_s=$median;
        p95_s=$sorted[[int][math]::Ceiling($sorted.Count * .95)-1]; max_s=$sorted[-1] }
}
function Get-Envelope($Lines) {
    $points = @($Lines | ForEach-Object { $_.box | ForEach-Object { ,$_ } })
    if (-not $points.Count) { return 'none' }
    $xx = @($points | ForEach-Object { $_[0] }); $yy = @($points | ForEach-Object { $_[1] })
    '{0},{1},{2},{3}' -f ($xx | Measure-Object -Minimum).Minimum, ($yy | Measure-Object -Minimum).Minimum,
        ($xx | Measure-Object -Maximum).Maximum, ($yy | Measure-Object -Maximum).Maximum
}
$rows = @(); $imageCount = 0; $sameBoxes = 0; $sameEnvelope = 0; $failed = @()
foreach ($file in Get-ChildItem -LiteralPath $GpuDir -Filter 'c*.json') {
    $g = Get-Content -Raw -LiteralPath $file.FullName | ConvertFrom-Json
    if ($g.status -ne 'completed') { $failed += $g.case_id; continue }
    $c = Get-Content -Raw -LiteralPath (Join-Path $CpuDir $file.Name) | ConvertFrom-Json
    if ($c.status -ne 'completed' -or $g.images.Count -ne $c.images.Count) { throw 'Unpaired cases' }
    for ($i=0; $i -lt $g.images.Count; $i++) {
        if ($g.images[$i].path -ne $c.images[$i].path) { throw 'Unpaired images' }
        $imageCount++
        if (($g.images[$i].lines | ConvertTo-Json -Depth 8 -Compress) -eq
            ($c.images[$i].lines | ConvertTo-Json -Depth 8 -Compress)) { $sameBoxes++ }
        if ((Get-Envelope $g.images[$i].lines) -eq (Get-Envelope $c.images[$i].lines)) { $sameEnvelope++ }
    }
    $abPath = Join-Path '.artifacts/gemini-ab-177-v1/cases' $file.Name
    $oldCPath = Join-Path '.artifacts/easyocr-union-gemini-177-v1/cases' $file.Name
    $ab = if (Test-Path -LiteralPath $abPath) { Get-Content -Raw -LiteralPath $abPath | ConvertFrom-Json }
    $oldC = if (Test-Path -LiteralPath $oldCPath) { Get-Content -Raw -LiteralPath $oldCPath | ConvertFrom-Json }
    $rows += [pscustomobject]@{
        case_id=$g.case_id; cpu_s=($c.images | Measure-Object elapsed_s -Sum).Sum;
        gpu_s=($g.images | Measure-Object elapsed_s -Sum).Sum;
        common=($ab.status -eq 'completed' -and $oldC.status -eq 'completed');
        historical_gemini_s=$oldC.call.elapsed_s; historical_full_s=$ab.calls.A.elapsed_s
    }
}
$common = @($rows | Where-Object common)
$result = [ordered]@{
    scope='Detection remeasured; Gemini timings reused from historical calls, not an end-to-end rerun';
    completed=$rows.Count; failed=$failed; images=$imageCount; identical_boxes=$sameBoxes;
    identical_union_envelope=$sameEnvelope; cpu_detection=(Get-Stats $rows.cpu_s); gpu_detection=(Get-Stats $rows.gpu_s);
    common_cases=$common.Count; common_cpu_detection=(Get-Stats $common.cpu_s);
    common_gpu_detection=(Get-Stats $common.gpu_s); historical_full_gemini=(Get-Stats $common.historical_full_s);
    historical_crop_gemini=(Get-Stats $common.historical_gemini_s);
    estimated_gpu_plus_historical_gemini=(Get-Stats @($common | ForEach-Object { $_.gpu_s + $_.historical_gemini_s }))
}
$result | ConvertTo-Json -Depth 6
