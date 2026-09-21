$ErrorActionPreference = 'Stop'
$gemmaRoot = Join-Path $PSScriptRoot '../.artifacts/gemma-4-E4B'
$gemmaModel = Join-Path $gemmaRoot 'gemma-4-E4B-it-Q4_0.gguf'
$gemmaVision = Join-Path $gemmaRoot 'mmproj-gemma-4-E4B-it-BF16.gguf'
if (!(Test-Path -LiteralPath $gemmaModel) -or !(Test-Path -LiteralPath $gemmaVision)) {
    throw 'Gemma model or vision projector is missing.'
}
& llama-server -m $gemmaModel --mmproj $gemmaVision --host 127.0.0.1 --port 8766 --ctx-size 4096 --parallel 1 --n-gpu-layers 999 --batch-size 2048 --ubatch-size 2048 --image-max-tokens 560 --alias foodscan-gemma4-e4b
