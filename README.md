# HROne Employee Attendance & Analytics API

FastAPI + MongoDB implementation scaffold for the Employee Attendance & Analytics assignment.

## Setup (Windows PowerShell)

1. Install Python 3.11+ and MongoDB 6+.
2. Copy `.env.example` to `.env` and update the connection settings.
3. In this folder run:

```powershell
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

4. Visit http://127.0.0.1:8000/docs

The app creates its indexes on startup. Do not commit `.env`.

## Sample data

`sample_data/` contains small example JSON files. `sample_seed.py` imports them into the database configured in `.env`.
Run only against a development database because it clears the two target collections first.

## Before submission

Run the tests described in `REVIEW.md` and check the exact API contract in `openapi.yaml`. This project has not been run against the assignment grader or its 100,000-record dataset.
