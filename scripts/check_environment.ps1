# ==============================================================================
# JARVIS — Phase 2 Automated Environment Verification & Setup Script
# Checks (and optionally installs) Ollama, qwen2.5:3b, Tailscale, and Python venv
# ==============================================================================

Write-Host "=========================================================" -ForegroundColor Cyan
Write-Host "  JARVIS Environment Verification & Setup (Windows)      " -ForegroundColor Cyan
Write-Host "=========================================================" -ForegroundColor Cyan

# 1. Check Python Virtual Environment
if (Test-Path ".\.venv\Scripts\python.exe") {
    $pyVer = & .\.venv\Scripts\python.exe --version
    Write-Host "[OK] Python Virtual Environment ready: $pyVer" -ForegroundColor Green
} else {
    Write-Host "[!] Creating Python virtual environment..." -ForegroundColor Yellow
    python -m venv .venv
    & .\.venv\Scripts\python.exe -m pip install -r requirements.txt
}

# 2. Check .env file
if (Test-Path ".\.env") {
    Write-Host "[OK] .env configuration file exists." -ForegroundColor Green
} else {
    Copy-Item ".\.env.example" ".\.env"
    Write-Host "[OK] Created .env from .env.example." -ForegroundColor Green
}

# 3. Check Ollama
$ollamaCmd = Get-Command ollama -ErrorAction SilentlyContinue
if (-not $ollamaCmd) {
    $localOllama = "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe"
    if (Test-Path $localOllama) {
        $env:Path += ";$env:LOCALAPPDATA\Programs\Ollama"
        $ollamaCmd = Get-Command ollama -ErrorAction SilentlyContinue
    }
}

if ($ollamaCmd) {
    $ollamaVer = & ollama --version
    Write-Host "[OK] Ollama installed: $ollamaVer" -ForegroundColor Green
} else {
    Write-Host "[!] Ollama not found. You can install it via:" -ForegroundColor Yellow
    Write-Host "    winget install --id Ollama.Ollama -e --accept-source-agreements --accept-package-agreements" -ForegroundColor White
}

# 4. Check Tailscale
$tsCmd = Get-Command tailscale -ErrorAction SilentlyContinue
if (-not $tsCmd) {
    $programFilesTs = "C:\Program Files\Tailscale\tailscale.exe"
    if (Test-Path $programFilesTs) {
        $env:Path += ";C:\Program Files\Tailscale"
        $tsCmd = Get-Command tailscale -ErrorAction SilentlyContinue
    }
}

if ($tsCmd) {
    $tsVer = & tailscale version | Select-Object -First 1
    Write-Host "[OK] Tailscale installed: $tsVer" -ForegroundColor Green
} else {
    Write-Host "[!] Tailscale not found. Install it via:" -ForegroundColor Yellow
    Write-Host "    winget install --id Tailscale.Tailscale -e --accept-source-agreements --accept-package-agreements" -ForegroundColor White
}

Write-Host "=========================================================" -ForegroundColor Cyan
