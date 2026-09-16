# Setup script for the coating_kg project (Windows PowerShell)
# Usage:
#   PS> cd ./coating_kg
#   PS> .\scripts\setup_env.ps1

$ErrorActionPreference = "Stop"

# ---- 1. Check conda is available ----
$condaCmd = Get-Command conda -ErrorAction SilentlyContinue
if (-not $condaCmd) {
    Write-Host "conda not found in PATH." -ForegroundColor Red
    Write-Host "If you have Anaconda/Miniconda installed, run:" -ForegroundColor Yellow
    Write-Host "  & '$env:USERPROFILE\miniconda3\shell\condabin\conda-hook.ps1'" -ForegroundColor Yellow
    Write-Host "Or initialize conda for PowerShell once with:" -ForegroundColor Yellow
    Write-Host "  conda init powershell" -ForegroundColor Yellow
    Write-Host "Then close + reopen PowerShell and re-run this script." -ForegroundColor Yellow
    exit 1
}

# ---- 2. Create env if not exists ----
$envExists = (& conda env list) | Select-String -Pattern "^\s*coating\s"
if (-not $envExists) {
    Write-Host "===> Creating conda env 'coating' with Python 3.11..." -ForegroundColor Cyan
    & conda create -n coating python=3.11 -y
} else {
    Write-Host "===> conda env 'coating' already exists, skipping create." -ForegroundColor Yellow
}

# ---- 3. Install dependencies ----
Write-Host "`n===> Installing dependencies into 'coating' env..." -ForegroundColor Cyan
& conda run -n coating pip install -r requirements.txt

# ---- 4. Done ----
Write-Host "`n✅ Setup complete." -ForegroundColor Green
Write-Host "`nNext steps:" -ForegroundColor Cyan
Write-Host "  conda activate coating" -ForegroundColor White
Write-Host "  python scripts\test_qwen.py" -ForegroundColor White
Write-Host "  python scripts\smoke_test_mineru.py" -ForegroundColor White
