<#
.SYNOPSIS
Sets up the environment and starts the backend service.
#>

# Temporarily bypass execution policy for this process to allow running the activation script
Set-ExecutionPolicy -ExecutionPolicy Bypass -Scope Process -Force

# Automatically navigate to the directory where this script is located
Set-Location -Path $PSScriptRoot
Write-Host "Working directory set to: $PSScriptRoot" -ForegroundColor Yellow

if (-not (Test-Path -Path "venv")) {
    Write-Host "Creating Python virtual environment..." -ForegroundColor Cyan
    python -m venv venv
} else {
    Write-Host "Virtual environment already exists." -ForegroundColor Green
}

Write-Host "Activating virtual environment..." -ForegroundColor Cyan
.\venv\Scripts\Activate.ps1

Write-Host "Installing dependencies..." -ForegroundColor Cyan
pip install -r requirements.txt

Write-Host "Initializing Database (checking/creating tables)..." -ForegroundColor Cyan
python database.py

Write-Host "Ingesting Data Files into Database..." -ForegroundColor Cyan
python ingest_pipeline.py

Write-Host "Computing Hourly Traffic Profiles..." -ForegroundColor Cyan
python compute_hourly_profiles.py

Write-Host "Triggering Model Training Pipeline..." -ForegroundColor Cyan
python retrain_pipeline.py

Write-Host "Starting FastAPI Server..." -ForegroundColor Green
uvicorn api:app --host 0.0.0.0 --port 8000
