param([int]$Port = 8765, [ValidateRange(2048,32768)][int]$ContextSize = 8192)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$modelRoot = Join-Path $projectRoot 'data\runtime\local_model'
$serverPath = Join-Path $modelRoot 'llama\llama-server.exe'
$weightPath = Join-Path $modelRoot 'Qwen3-4B-Q4_K_M.gguf'
if (!(Test-Path -LiteralPath $serverPath) -or !(Test-Path -LiteralPath $weightPath)) {
    throw 'Local model files are missing. See docs/LOCAL_MODEL.md.'
}
$pidPath = Join-Path $modelRoot 'server.pid'
if (Test-Path -LiteralPath $pidPath) {
    $ownedProcessId = [int](Get-Content -LiteralPath $pidPath)
    $ownedProcess = Get-Process -Id $ownedProcessId -ErrorAction SilentlyContinue
    if ($ownedProcess -and $ownedProcess.Path -eq (Resolve-Path -LiteralPath $serverPath).Path) {
        Write-Output "Project local model is loading or running (PID $ownedProcessId)."
        exit 0
    }
}
try {
    $existing = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/v1/models" -TimeoutSec 2
    if ($existing.data.id -contains 'qwen3-4b-local') {
        Write-Output "Local model already running on 127.0.0.1:$Port"
        exit 0
    }
    throw "Another service is already using port $Port."
} catch [System.Net.WebException] {
    # No running local server; launch only our project binary.
}
$arguments = @('--model', ('"' + $weightPath + '"'), '--alias', 'qwen3-4b-local',
    '--host', '127.0.0.1', '--port', "$Port", '--device', 'Vulkan0',
    '--gpu-layers', '999', '--ctx-size', "$ContextSize", '--parallel', '1',
    '--reasoning', 'off', '--offline', '--cache-ram', '512',
    '--cors-origins', 'localhost', '--no-cors-credentials', '--api-key', 'local-only')
$process = Start-Process -FilePath $serverPath -ArgumentList $arguments -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $modelRoot 'server.stdout.log') `
    -RedirectStandardError (Join-Path $modelRoot 'server.stderr.log')
$process.Id | Set-Content -LiteralPath (Join-Path $modelRoot 'server.pid') -Encoding ascii
Write-Output "Started local model PID $($process.Id), listening only on 127.0.0.1:$Port."
