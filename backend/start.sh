#!/bin/bash

# Automatically navigate to the directory where this script is located
cd "$(dirname "$0")" || exit 1
echo "Working directory set to: $(pwd)"

echo "Creating Python virtual environment..."
python3 -m venv venv

echo "Activating virtual environment..."
source venv/bin/activate

echo "Installing dependencies..."
pip install -r requirements.txt

echo "Initializing Database (checking/creating tables)..."
python database.py

echo "Triggering Model Training Pipeline..."
python retrain_pipeline.py

echo "Starting FastAPI Server..."
uvicorn api:app --host 0.0.0.0 --port 8000
