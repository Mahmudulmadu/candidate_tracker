# Start the Interview Tracker, creating the virtualenv and installing
# dependencies the first time. Run from the project folder:
#
#     .\start.ps1
#
# If PowerShell refuses to run it:
#     Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

if (-not (Test-Path ".venv")) {
    Write-Host "Creating virtual environment..." -ForegroundColor Cyan
    python -m venv .venv
    & ".\.venv\Scripts\python.exe" -m pip install --upgrade pip --quiet
    Write-Host "Installing dependencies (this takes a minute)..." -ForegroundColor Cyan
    & ".\.venv\Scripts\python.exe" -m pip install -r requirements.txt
}

if (-not (Test-Path ".env")) {
    Write-Host ".env is missing. Copy .env.example to .env and fill in your PostgreSQL password." -ForegroundColor Yellow
    exit 1
}

& ".\.venv\Scripts\python.exe" run.py
