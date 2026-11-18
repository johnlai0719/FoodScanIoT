$ErrorActionPreference = 'Stop'
$tailnetIP = (& tailscale ip -4 | Select-Object -First 1).Trim()
$tailnetDNS = ((& tailscale status --json | ConvertFrom-Json).Self.DNSName).TrimEnd('.')
if (-not $tailnetIP -or -not $tailnetDNS) {
    throw 'Connect this PC to Tailscale first.'
}

$port = 8086
$env:REACT_NATIVE_PACKAGER_HOSTNAME = $tailnetIP
$env:EXPO_PACKAGER_PROXY_URL = "http://${tailnetIP}:$port"
$env:EXPO_PUBLIC_DEMO_TRACE_URL = "http://${tailnetDNS}:8787/api/live"

Push-Location (Join-Path $PSScriptRoot '../APP')
try {
    if (-not (Test-Path 'node_modules/expo/bin/cli')) {
        npm.cmd ci --no-audit --no-fund
        if ($LASTEXITCODE -ne 0) { throw 'Expo dependency install failed.' }
    }
    Write-Host "Expo Go: exp://${tailnetIP}:$port"
    node node_modules/expo/bin/cli start --go --port $port --host lan
    if ($LASTEXITCODE -ne 0) { throw 'Expo failed to start.' }
} finally {
    Pop-Location
}
