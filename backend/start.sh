#!/bin/bash

# Automatically navigate to the directory where this script is located
cd "$(dirname "$0")" || exit 1
echo "Working directory set to: $(pwd)"

if [ ! -d "venv" ]; then
    echo "Creating Python virtual environment..."
    python3 -m venv venv
else
    echo "Virtual environment already exists."
fi

echo "Activating virtual environment..."
source venv/bin/activate

echo "Installing dependencies..."
pip install -r requirements.txt

echo "Initializing Database (checking/creating tables)..."
python database.py

echo "Ingesting Data Files into Database..."
python ingest_pipeline.py

echo "Computing Hourly Traffic Profiles..."
python compute_hourly_profiles.py

echo "Triggering Model Training Pipeline..."
python retrain_pipeline.py

echo "Starting FastAPI Server on port 8079..."
uvicorn api:app --host 0.0.0.0 --port 8079
