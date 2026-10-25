# ==============================================================================
# JARVIS — Safe SQLite Database Backup & Restore Utility
# Uses SQLite's online backup API via Python so WAL transactions are never corrupted
# ==============================================================================

param(
    [ValidateSet("backup", "restore")]
    [string]$Mode = "backup",
    [string]$RestoreFile = ""
)

$ProjectRoot = (Resolve-Path "$PSScriptRoot\..").Path
Set-Location $ProjectRoot

$BackupDir = Join-Path $ProjectRoot "backups"
New-Item -ItemType Directory -Force -Path $BackupDir | Out-Null

if ($Mode -eq "backup") {
    $Timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $DestFile = Join-Path $BackupDir "jarvis_backup_$Timestamp.db"
    $PyCmd = "import sqlite3; src = sqlite3.connect('data/jarvis.db'); dst = sqlite3.connect(r'$DestFile'); src.backup(dst); dst.close(); src.close()"
    & .\.venv\Scripts\python.exe -c $PyCmd
    Write-Host "[OK] Safe online SQLite backup created at: $DestFile" -ForegroundColor Green
} else {
    if (-not $RestoreFile -or -not (Test-Path $RestoreFile)) {
        Write-Host "[!] Specify a valid backup file with -RestoreFile <path>" -ForegroundColor Red
        exit 1
    }
    $PyCmd = "import sqlite3; src = sqlite3.connect(r'$RestoreFile'); dst = sqlite3.connect('data/jarvis.db'); src.backup(dst); dst.close(); src.close()"
    & .\.venv\Scripts\python.exe -c $PyCmd
    Write-Host "[OK] Restored database from: $RestoreFile" -ForegroundColor Green
}
