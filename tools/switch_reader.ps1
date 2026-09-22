<#
.SYNOPSIS
    切換辨識後端：主線 vlcrop 或競賽版 EasyOCR→Gemini。互斥，且會釋放 VRAM。

.DESCRIPTION
    這張卡只有 8188 MiB，兩條路徑都要 GPU，**擠在一起誰都跑不好**。所以切換不只
    是換一個行程，而是把另一邊佔的 VRAM 真的放掉。

    涉及三個服務：

        :8180  reader 本體（主線 vlcrop 或競賽版，二選一）
        :8177  HunyuanOCR  llama-server ┐ 只有主線 vlcrop 需要
        :8179  Qwen3.5-2B  llama-server ┘ 競賽版完全用不到

    實測（2026-09-22，單張同一圖）：

        主線全開          7726 MiB used / 232 MiB free
        只留兩個 llama    4280 MiB used
        競賽版 + llama    5944 MiB used（EasyOCR 自己吃 1664）
        競賽版、llama 關   ← 這支現在會做到，EasyOCR 有整張卡可用

    ⚠ **232 MiB free 是關鍵數字。** 主線全開時若再起 EasyOCR，
      `torch.cuda.is_available()` 仍是 True、`gpu=True` 照樣傳進去，然後在配置
      記憶體時炸掉或被迫退回 CPU——不會有明顯的錯誤訊息，只會變得很慢。
      這就是為什麼必須互斥，不只是為了乾淨。

    代價：切回主線時 llama-server 要重新暖機（約 1 分鐘）。這支會等到它們就緒
    才起 reader，所以你看到提示就是真的可以用了。

.PARAMETER Target
    vlcrop      主線：起 8177/8179，再起 PP-OCR → HunyuanOCR → Qwen
    competition 競賽版：關掉 8177/8179 釋放 VRAM，再起 EasyOCR → Gemini 2.5 Flash
    stop        全部關掉，不留任何東西佔 VRAM
    status      誰在跑、VRAM 現況

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
$ReaderPort = 8180
$ModelPorts = @(8177, 8179)
$CompetitionRoot = 'D:\FoodScanIot\_worktrees\adi-multimodal-compliance'
$CompetitionPython = Join-Path $CompetitionRoot '.venv-easyocr-cuda-clean\Scripts\python.exe'
$MainlineRoot = 'D:\FoodScanIot\APP-sync-server'

function Get-PortOwner([int]$Port) {
    $line = netstat -ano | Select-String ":$Port\s" | Select-String 'LISTENING' | Select-Object -First 1
    if (-not $line) { return $null }
    $procId = ($line.ToString() -split '\s+')[-1]
    try { return Get-Process -Id $procId -ErrorAction Stop } catch { return $null }
}

function Get-Vram {
    try {
        $o = (& nvidia-smi --query-gpu=memory.used,memory.free --format=csv,noheader) -join ''
        return $o.Trim()
    } catch { return '(nvidia-smi 不可用)' }
}

function Stop-OnPort([int]$Port, [string]$Label) {
    $owner = Get-PortOwner $Port
    if (-not $owner) { return $false }
    Write-Host "  關掉 :$Port $Label PID $($owner.Id) ($($owner.ProcessName))"
    Stop-Process -Id $owner.Id -Force
    # 等埠真的釋放。不等的話下一個會 address in use，而那個錯誤看起來像程式壞了。
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Milliseconds 300
        if (-not (Get-PortOwner $Port)) { return $true }
    }
    throw ":$Port 在 9 秒後仍被佔用，請自行確認 PID $($owner.Id)。"
}

function Stop-ModelServers {
    # 殺掉其中一個，start_models.py 的看守迴圈會偵測到並在 finally 收掉另一個。
    # 仍然兩個都明確處理：看守腳本可能沒在跑（例如上次是手動起的）。
    $any = $false
    foreach ($p in $ModelPorts) { if (Stop-OnPort $p 'llama-server') { $any = $true } }
    if ($any) {
        # 看守腳本最多 5 秒才會發現，等它自己退出，否則它會在背景再收一次
        Start-Sleep -Seconds 6
        Get-Process -Name python -ErrorAction SilentlyContinue | ForEach-Object {
            try {
                $cmd = (Get-CimInstance Win32_Process -Filter "ProcessId=$($_.Id)").CommandLine
                if ($cmd -and $cmd -match 'start_models\.py') {
                    Write-Host "  關掉 start_models 看守行程 PID $($_.Id)"
                    Stop-Process -Id $_.Id -Force
                }
            } catch {}
        }
    } else {
        Write-Host "  8177/8179 本來就沒在跑。"
    }
}

function Start-ModelServers {
    $missing = $ModelPorts | Where-Object { -not (Get-PortOwner $_) }
    if (-not $missing) {
        Write-Host "  8177/8179 已在執行，沿用（省去約 1 分鐘暖機）。"
        return
    }
    Write-Host "  啟動 llama-server（8177/8179），暖機約 1 分鐘..."
    Start-Process -FilePath 'python' -ArgumentList 'reader/start_models.py' `
        -WorkingDirectory $MainlineRoot
    for ($i = 0; $i -lt 90; $i++) {
        Start-Sleep -Seconds 2
        if (-not ($ModelPorts | Where-Object { -not (Get-PortOwner $_) })) {
            Write-Host "  8177/8179 都起來了。"
            return
        }
    }
    throw "llama-server 在 3 分鐘後仍未就緒。手動跑 python reader/start_models.py 看錯誤。"
}

function Show-Status {
    $owner = Get-PortOwner $ReaderPort
    if (-not $owner) {
        Write-Host "8180：沒有 reader 在跑"
    } else {
        Write-Host "8180：PID $($owner.Id) ($($owner.ProcessName))"
        try {
            # 問它自己是誰。兩個 reader 都是 python.exe，從行程名分不出來。
            $h = Invoke-RestMethod -Uri "http://localhost:$ReaderPort/health" -TimeoutSec 5
            Write-Host "  reader   : $($h.reader)"
            Write-Host "  pipeline : $($h.pipeline)  $($h.detail)"
        } catch {
            Write-Host "  （/health 沒有回應，可能還在暖機）"
        }
    }
    foreach ($p in $ModelPorts) {
        $o = Get-PortOwner $p
        Write-Host ("{0}：{1}" -f $p, $(if ($o) { "PID $($o.Id) ($($o.ProcessName))" } else { '沒在跑' }))
    }
    Write-Host "VRAM used/free：$(Get-Vram)"
}

switch ($Target) {
    'status' { Show-Status; return }
    'stop' {
        Write-Host '全部關掉...'
        Stop-OnPort $ReaderPort 'reader' | Out-Null
        Stop-ModelServers
        Write-Host "VRAM used/free：$(Get-Vram)"
        return
    }
}

Write-Host "切換到 $Target ..."
Stop-OnPort $ReaderPort 'reader' | Out-Null

if ($Target -eq 'competition') {
    # 關掉 llama-server：競賽版用不到它們，而它們佔的 VRAM 正是 EasyOCR 要用的。
    Stop-ModelServers
    if (-not (Test-Path -LiteralPath $CompetitionPython)) {
        throw "找不到 $CompetitionPython。先跑 tools/setup_easyocr_gpu.ps1。"
    }
    # 把 Cloud 的共享密鑰帶進去，否則這支的 /read 不驗證金鑰（/health 會顯示
    # auth: disabled）。主線 reader 是由外部環境提供同一個變數，這裡對齊它。
    $envFile = Join-Path $MainlineRoot 'server\.env'
    if (Test-Path -LiteralPath $envFile) {
        $line = Select-String -LiteralPath $envFile -Pattern '^API_SHARED_SECRET\s*=' |
                Select-Object -First 1
        if ($line) {
            $env:API_SHARED_SECRET = ($line.ToString() -split '=', 2)[1].Trim().Trim('"').Trim("'")
            Write-Host '  已帶入 API_SHARED_SECRET（與 Cloud 同一把）'
        }
    }
    if (-not $env:API_SHARED_SECRET) {
        Write-Host '  註：找不到 API_SHARED_SECRET，這支的 /read 將不驗證金鑰。'
    }
    Write-Host '  啟動競賽版 reader（EasyOCR → Gemini 2.5 Flash）...'
    Start-Process -FilePath $CompetitionPython -ArgumentList 'tools/reader_competition.py' `
        -WorkingDirectory $CompetitionRoot
} else {
    Start-ModelServers
    Write-Host '  啟動主線 vlcrop reader...'
    Start-Process -FilePath 'python' -ArgumentList 'reader/service.py' `
        -WorkingDirectory $MainlineRoot
}

Write-Host ''
Write-Host "VRAM used/free：$(Get-Vram)"
Write-Host '已送出啟動指令。reader 本身仍要暖機（競賽版載 EasyOCR 數十秒、'
Write-Host 'vlcrop 約 43 秒），用這個確認就緒：'
Write-Host '    ./tools/switch_reader.ps1 status'
