# ==============================================================================
# Local Media Hub - Smart Unified Launcher
# Automatically checks & launches ComfyUI (AMD GPU engine) and Local Media Hub.
# ==============================================================================

$ErrorActionPreference = "SilentlyContinue"
$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$hubUrl = "http://127.0.0.1:8000"
$healthUrl = "$hubUrl/api/v1/system/health"
$comfyStatsUrl = "http://127.0.0.1:8188/system_stats"
$comfyDir = "C:\ComfyUI\ComfyUI_windows_portable"

function Test-UrlOnline($url) {
    try {
        $res = Invoke-RestMethod -Uri $url -TimeoutSec 1 -Method Get -ErrorAction Stop
        return ($null -ne $res)
    } catch {
        return $false
    }
}

# 1. Inicia ComfyUI com AMD GPU se não estiver ativo
if (-not (Test-UrlOnline $comfyStatsUrl)) {
    if (Test-Path $comfyDir) {
        $psiComfy = New-Object System.Diagnostics.ProcessStartInfo
        $psiComfy.FileName = "$comfyDir\python_embeded\python.exe"
        $psiComfy.Arguments = "-s ComfyUI\main.py --windows-standalone-build --listen 127.0.0.1 --port 8188 --disable-triton-backend --use-split-cross-attention"
        $psiComfy.WorkingDirectory = $comfyDir
        $psiComfy.WindowStyle = [System.Diagnostics.ProcessWindowStyle]::Minimized
        [System.Diagnostics.Process]::Start($psiComfy) | Out-Null
    }
}

# 2. Inicia Local Media Hub se não estiver ativo
if (-not (Test-UrlOnline $healthUrl)) {
    $psiHub = New-Object System.Diagnostics.ProcessStartInfo
    $psiHub.FileName = "python"
    $psiHub.Arguments = "main.py"
    $psiHub.WorkingDirectory = $projectDir
    $psiHub.WindowStyle = [System.Diagnostics.ProcessWindowStyle]::Minimized
    [System.Diagnostics.Process]::Start($psiHub) | Out-Null

    # Aguarda o Hub responder
    $maxAttempts = 30
    $attempt = 0
    while ($attempt -lt $maxAttempts) {
        Start-Sleep -Milliseconds 400
        if (Test-UrlOnline $healthUrl) {
            break
        }
        $attempt++
    }
}

# 3. Abre a interface web no navegador padrão
Start-Process $hubUrl
