# Forecasting Model Backend - QA Setup Guide


This document contains step-by-step instructions for the QA team to set up and run the Forecasting Model Backend locally or on a test server. The frontend and other components are omitted.

> **Important Note:** The forecasting model has already been trained using the previous 3 years of ICP data, and the resulting predictions and base tables are already present on the SQL Server. Because of this, no raw data files are provided in this release, and no data ingestion or network training steps are necessary.

## Prerequisites

- **Python 3.x** (Python 3.10+ recommended)
- **pip** (Python package manager)

---

## 1. Extract the Source Code

Assuming you have received the release package, extract the backend files to your desired directory and navigate into it.

```bash
cd backend
```

## 2. Setup Virtual Environment (Recommended)

It is highly recommended to use a virtual environment to isolate the specific dependencies.

```bash
# Create a virtual environment named 'venv'
python -m venv venv

# Activate it (Linux/macOS)
source venv/bin/activate

# Activate it (Windows PowerShell)
# .\venv\Scripts\Activate.ps1

# Activate it (Windows Command Prompt)
# .\venv\Scripts\activate.bat
```

## 3. Install Requirements

Install all exact dependencies needed by the application from the frozen `requirements.txt`. This ensures the environment runs out-of-the-box identically to development.

```bash
pip install -r requirements.txt
```

## 4. Environment Variables Configuration

The application requires environment variables to connect to the database securely. We have provided an `.env.example` file in the directory. You should copy it to a new file named `.env`:

```bash
cp .env.example .env
```

Open the newly created `.env` file and replace the boilerplate placeholders with your actual QA database credentials.

> **Note:** Ensure your local machine or test server has network access to the database server IP address/port.

## 5. Running the Application

Once dependencies are installed and the `.env` file is ready, start the application using Uvicorn.

```bash
uvicorn api:app --host 0.0.0.0 --port 8079
```

The backend API will be available at `http://localhost:8079` (or the server's IP address if running on a remote host).

## 6. Testing the API

You can access the auto-generated interactive API documentation (Swagger UI) at:
`http://localhost:8079/docs`

This interface allows you to test endpoints directly from your browser without needing an external client like Postman, though Postman can also be used.

---

## 7. Notes on Data Ingestion and Retraining

You might notice two specific scripts in the codebase: `ingest_pipeline.py` and `retrain_pipeline.py`. 

- **Data Ingestion (`ingest_pipeline.py`)**: This script processes raw CSV/Excel data files and imports them into the SQL server for the model to learn from.
- **Model Retraining (`retrain_pipeline.py`)**: This script triggers the Prophet machine learning network to re-analyze the database and generate fresh future predictions.

**Why QA cannot test these right now:**
QA is not expected to test these scripts during this phase because they strictly require large batches of raw, unformatted data files (e.g., new Excel/CSV files containing thousands of rows) to function properly. Since no new data files are provided in this release—and the SQL database is already fully pre-seeded with 3 years of historical ICP data and active predictions—running these pipelines is both unnecessary and impossible to validate without a valid new dataset.
