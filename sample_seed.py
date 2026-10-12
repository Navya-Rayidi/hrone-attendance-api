import json, os
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv()
client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"))
db = client[os.getenv("MONGO_DB", "hrone_attendance")]
base = Path(__file__).parent

def load_employees():
    rows = json.loads((base / "sample_data/employees.json").read_text(encoding="utf-8"))
    for row in rows:
        row["created_at"] = datetime.now(timezone.utc).replace(microsecond=0)
    db.employees.delete_many({})
    if rows:
        db.employees.insert_many(rows)
    print(f"Loaded {len(rows)} employees")

def load_attendance():
    rows = json.loads((base / "sample_data/attendance_logs.json").read_text(encoding="utf-8"))
    for row in rows:
        for field in ("punch_in", "punch_out"):
            if row.get(field):
                row[field] = datetime.fromisoformat(row[field].replace("Z", "+00:00"))
        row["history"] = []
    db.attendance.delete_many({})
    if rows:
        db.attendance.insert_many(rows)
    print(f"Loaded {len(rows)} attendance records")

if __name__ == "__main__":
    load_employees()
    load_attendance()
