# ==============================================================================
# JARVIS — Startup Script (Windows PowerShell)
# Starts Ollama (if not running), checks Tailscale Funnel, and launches FastAPI
# ==============================================================================

$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot\..

Write-Host "=========================================================" -ForegroundColor Cyan
Write-Host "  Starting JARVIS Personal WhatsApp AI Assistant...      " -ForegroundColor Cyan
Write-Host "=========================================================" -ForegroundColor Cyan

# Ensure local Ollama and Tailscale paths are in PATH if installed in default locations
if (Test-Path "$env:LOCALAPPDATA\Programs\Ollama") {
    $env:Path += ";$env:LOCALAPPDATA\Programs\Ollama"
}
if (Test-Path "C:\Program Files\Tailscale") {
    $env:Path += ";C:\Program Files\Tailscale"
}

# 1. Start Ollama Server in background if installed and not already listening on 11434
$ollamaListening = Get-NetTCPConnection -LocalPort 11434 -State Listen -ErrorAction SilentlyContinue
if (-not $ollamaListening) {
    if (Get-Command ollama -ErrorAction SilentlyContinue) {
        Write-Host "[*] Starting local Ollama server..." -ForegroundColor Yellow
        Start-Process -FilePath "ollama" -ArgumentList "serve" -WindowStyle Hidden
        Start-Sleep -Seconds 3
    } else {
        Write-Host "[!] Ollama not installed yet. Shortcut commands will still work." -ForegroundColor Yellow
    }
} else {
    Write-Host "[OK] Ollama server is running on 127.0.0.1:11434" -ForegroundColor Green
}

# 2. Launch FastAPI Server bound strictly to 127.0.0.1:8000
Write-Host "[*] Starting JARVIS FastAPI server on http://127.0.0.1:8000 ..." -ForegroundColor Green
& .\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
