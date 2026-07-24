@echo off
cd /d C:\xampp8.2\htdocs\Forecastsing-Model\backend
C:\xampp8.2\htdocs\Forecastsing-Model\backend\venv\Scripts\python.exe ingest_pipeline.py
exit /b %errorlevel%
