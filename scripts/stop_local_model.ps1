$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$modelRoot = Join-Path $projectRoot 'data\runtime\local_model'
$pidPath = Join-Path $modelRoot 'server.pid'
if (!(Test-Path -LiteralPath $pidPath)) { Write-Output 'No project model PID recorded.'; exit 0 }
$modelProcessId = [int](Get-Content -LiteralPath $pidPath)
$process = Get-Process -Id $modelProcessId -ErrorAction SilentlyContinue
if ($process) {
    $expectedPath = (Resolve-Path -LiteralPath (Join-Path $modelRoot 'llama\llama-server.exe')).Path
    if ($process.Path -ne $expectedPath) { throw 'Recorded PID no longer belongs to the project model server.' }
    Stop-Process -Id $modelProcessId
}
Remove-Item -LiteralPath $pidPath
Write-Output 'Project local model stopped.'
