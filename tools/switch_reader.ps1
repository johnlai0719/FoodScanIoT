<#
.SYNOPSIS
    切換辨識後端：主線 vlcrop 或競賽版 EasyOCR→Gemini。

.DESCRIPTION
    兩個 reader **聽同一個埠（8180）**，同時只會有一個在跑——它們搶同一張 GPU，
    本來就不該並存。聽同一個埠的好處是 Cloud 連 READER_URL 都不用改，
    切換純粹是「換一個行程起來」。

    所以這支做的事很簡單：先把佔著 8180 的那個關掉，再起你要的那個。

    ⚠ 主線 vlcrop 還需要兩個模型伺服器（8177 HunyuanOCR、8179 Qwen），
      它們由 reader/start_models.py 起。這支**不會**動它們：它們不吃滿 VRAM
      的時候留著沒關係，而反覆重啟 llama-server 要多等一分鐘暖機。
      競賽版不需要它們。

    ⚠ 切換後 Cloud 回報的 vision_backend 仍是 "vlcrop"，那只是「走 reader
      這條路」的意思。要知道實際是誰做的，看 /health 的 reader 欄位，
      或分析回應裡的 _meta.reader。

.PARAMETER Target
    vlcrop      主線：PP-OCR → HunyuanOCR → Qwen（在 APP-sync-server）
    competition 競賽版：EasyOCR 定位裁切 → Gemini 2.5 Flash（在本分支）
    stop        只關掉，不起新的
    status      只看現在誰在跑

.EXAMPLE
    ./tools/switch_reader.ps1 competition
    ./tools/switch_reader.ps1 vlcrop
    ./tools/switch_reader.ps1 status
#>
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('vlcrop', 'competition', 'stop', 'status')]
    [string]$Target
)

$ErrorActionPreference = 'Stop'
$Port = 8180
$CompetitionRoot = 'D:\FoodScanIot\_worktrees\adi-multimodal-compliance'
$CompetitionPython = Join-Path $CompetitionRoot '.venv-easyocr-cuda-clean\Scripts\python.exe'
$MainlineRoot = 'D:\FoodScanIot\APP-sync-server'

function Get-PortOwner {
    $line = netstat -ano | Select-String ":$Port\s" | Select-String 'LISTENING' | Select-Object -First 1
    if (-not $line) { return $null }
    $procId = ($line.ToString() -split '\s+')[-1]
    try { return Get-Process -Id $procId -ErrorAction Stop } catch { return $null }
}

function Show-Status {
    $owner = Get-PortOwner
    if (-not $owner) {
        Write-Host "8180：沒有服務在跑"
        return
    }
    Write-Host "8180：PID $($owner.Id) ($($owner.ProcessName))"
    try {
        # 問它自己是誰。用 /health 的 reader 欄位而不是猜行程參數——
        # 兩個 reader 都是 python.exe，從行程名分不出來。
        $h = Invoke-RestMethod -Uri "http://localhost:$Port/health" -TimeoutSec 5
        Write-Host "  reader   : $($h.reader)"
        Write-Host "  pipeline : $($h.pipeline)  $($h.detail)"
    } catch {
        Write-Host "  （/health 沒有回應，可能還在暖機）"
    }
}

function Stop-Reader {
    $owner = Get-PortOwner
    if (-not $owner) {
        Write-Host "8180 本來就沒有服務在跑。"
        return
    }
    Write-Host "關掉 PID $($owner.Id) ($($owner.ProcessName))..."
    Stop-Process -Id $owner.Id -Force
    # 等埠真的釋放。不等的話下一個會因為 address in use 起不來，
    # 而那個錯誤訊息看起來像程式壞了。
    for ($i = 0; $i -lt 20; $i++) {
        Start-Sleep -Milliseconds 300
        if (-not (Get-PortOwner)) { Write-Host "8180 已釋放。"; return }
    }
    throw "8180 在 6 秒後仍被佔用，請自行確認 PID $($owner.Id)。"
}

switch ($Target) {
    'status' { Show-Status; return }
    'stop'   { Stop-Reader; return }
}

Stop-Reader

if ($Target -eq 'competition') {
    if (-not (Test-Path -LiteralPath $CompetitionPython)) {
        throw "找不到 $CompetitionPython。先跑 tools/setup_easyocr_gpu.ps1。"
    }
    Write-Host "啟動競賽版 reader（EasyOCR → Gemini 2.5 Flash）..."
    # 把 Cloud 的共享密鑰帶進去，否則這支的 /read 是不驗證的（/health 會顯示
    # auth: disabled）。主線 reader 是由外部環境提供同一個變數，這裡對齊它。
    $envFile = Join-Path $MainlineRoot 'server\.env'
    if (Test-Path -LiteralPath $envFile) {
        $line = Select-String -LiteralPath $envFile -Pattern '^API_SHARED_SECRET\s*=' |
                Select-Object -First 1
        if ($line) {
            $env:API_SHARED_SECRET = ($line.ToString() -split '=', 2)[1].Trim().Trim('"').Trim("'")
            Write-Host "  已帶入 API_SHARED_SECRET（與 Cloud 同一把）"
        }
    }
    if (-not $env:API_SHARED_SECRET) {
        Write-Host "  註：找不到 API_SHARED_SECRET，這支的 /read 將不驗證金鑰。"
    }
    Start-Process -FilePath $CompetitionPython `
        -ArgumentList 'tools/reader_competition.py' `
        -WorkingDirectory $CompetitionRoot
} else {
    Write-Host "啟動主線 vlcrop reader..."
    Write-Host "  註：它需要 8177／8179 兩個模型伺服器。沒起的話先跑："
    Write-Host "      python reader/start_models.py"
    Start-Process -FilePath 'python' `
        -ArgumentList 'reader/service.py' `
        -WorkingDirectory $MainlineRoot
}

Write-Host ""
Write-Host "已送出啟動指令。暖機需要時間（競賽版載 EasyOCR 約數十秒、"
Write-Host "vlcrop 約 43 秒），用這個確認就緒："
Write-Host "    ./tools/switch_reader.ps1 status"
