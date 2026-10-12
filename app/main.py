from __future__ import annotations

import os, re, calendar
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, Literal
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Path
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pymongo import MongoClient, ASCENDING, DESCENDING
from pymongo.errors import DuplicateKeyError
from pymongo.collection import ReturnDocument

load_dotenv()
TZ = ZoneInfo("Asia/Kolkata")
UTC = timezone.utc
#MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
#MONGO_DB = os.getenv("MONGO_DB", "hrone_attendance")
#client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=3000)
#db = client[MONGO_DB]
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB = os.getenv("MONGO_DB", "hrone_attendance")

# Try real MongoDB, if not running use in-memory mock
try:
    temp_client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=1000)
    temp_client.admin.command("ping")
    client = temp_client
    print("✅ Connected to real MongoDB")
except Exception as e:
    print(f"⚠️ MongoDB not running ({e}), using in-memory mock DB")
    import mongomock
    client = mongomock.MongoClient()

db = client[MONGO_DB]
employees = db["employees"]
attendance = db["attendance"]
PRESENCE = {"PRESENT", "WFH", "ON_DUTY"}
STATUSES = {"PRESENT", "ABSENT", "LEAVE", "WFH", "ON_DUTY"}

app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0")

#@app.on_event("startup")
#def create_indexes():
 #   employees.create_index([("emp_code", ASCENDING)], unique=True, name="uq_emp_code")
  #  employees.create_index([("department", ASCENDING), ("joined_on", ASCENDING)], name="department_joined")
   # attendance.create_index([("emp_code", ASCENDING), ("date", ASCENDING)], unique=True, name="uq_emp_date")
    #attendance.create_index([("date", DESCENDING), ("emp_code", ASCENDING)], name="date_emp")
    #attendance.create_index([("status", ASCENDING), ("date", DESCENDING)], name="status_date")
    #attendance.create_index([("punch_in", DESCENDING)], name="punch_in_desc")
@app.on_event("startup")
def create_indexes():
    try:
        employees.create_index([("emp_code", ASCENDING)], unique=True, name="uq_emp_code")
        employees.create_index([("department", ASCENDING), ("joined_on", ASCENDING)], name="department_joined")
        attendance.create_index([("emp_code", ASCENDING), ("date", ASCENDING)], unique=True, name="uq_emp_date")
        attendance.create_index([("date", DESCENDING), ("emp_code", ASCENDING)], name="date_emp")
        attendance.create_index([("status", ASCENDING), ("date", DESCENDING)], name="status_date")
        attendance.create_index([("punch_in", DESCENDING)], name="punch_in_desc")
    except Exception as ex:
        print(f"Index creation skipped: {ex}")

def round_half_up(value, places=2):
    if value is None: return None
    q = Decimal("1").scaleb(-places)
    return float(Decimal(str(value)).quantize(q, rounding=ROUND_HALF_UP))

def parse_date(value: str) -> date:
    try: return date.fromisoformat(value)
    except Exception: raise HTTPException(422, "Date must be YYYY-MM-DD")

def month_bounds(month: str):
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
        raise HTTPException(422, "month must be YYYY-MM")
    year, mon = map(int, month.split("-"))
    start = date(year, mon, 1)
    end = date(year, mon, calendar.monthrange(year, mon)[1])
    return start, end

def working_days(start: date, end: date) -> int:
    if start > end: return 0
    n = 0
    cur = start
    while cur <= end:
        if cur.weekday() < 5: n += 1
        cur += timedelta(days=1)
    return n

def epoch_ms(value):
    if value is None: return None
    if value.tzinfo is None: value = value.replace(tzinfo=UTC)
    return int(value.timestamp() * 1000)

def parse_ms(value):
    if type(value) is not int or value < 100_000_000_000 or value > 4_102_444_800_000:
        raise HTTPException(422, "Timestamp must be an epoch-millisecond integer")
    return datetime.fromtimestamp(value // 1000, UTC).replace(microsecond=0)

def db_dt(value):
    return value.astimezone(UTC).replace(tzinfo=None, microsecond=0) if value else None

def utc_aware(value):
    if value is None: return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

def local_date_for_instant(instant, employee):
    local = utc_aware(instant).astimezone(TZ)
    day = local.date()
    shift_end = time.fromisoformat(employee["shift_end"])
    shift_start = time.fromisoformat(employee["shift_start"])
    if shift_end <= shift_start and local.time().replace(tzinfo=None) < shift_end:
        day -= timedelta(days=1)
    return day

def shift_bounds(day: date, employee):
    start_t = time.fromisoformat(employee["shift_start"])
    end_t = time.fromisoformat(employee["shift_end"])
    start = datetime.combine(day, start_t, TZ)
    end_day = day + timedelta(days=1) if end_t <= start_t else day
    end = datetime.combine(end_day, end_t, TZ)
    return start.astimezone(UTC), end.astimezone(UTC)

def late_for(punch_in, day, employee):
    if punch_in is None: return 0
    start, _ = shift_bounds(day, employee)
    seconds = int((utc_aware(punch_in) - start).total_seconds())
    return seconds // 60 if seconds > 600 else 0

def derived(punch_in, punch_out, day, employee):
    late = late_for(punch_in, day, employee)
    if punch_in is None or punch_out is None:
        return {"late_minutes": late, "work_hours": None, "overtime_minutes": 0, "half_day": False}
    seconds = int((utc_aware(punch_out) - utc_aware(punch_in)).total_seconds())
    hours = round_half_up(seconds / 3600, 2)
    _, shift_end = shift_bounds(day, employee)
    extra = int((utc_aware(punch_out) - shift_end).total_seconds()) // 60
    overtime = extra if extra >= 30 else 0
    return {"late_minutes": late, "work_hours": hours, "overtime_minutes": overtime, "half_day": hours < 4.5}

def serialize_employee(row):
    return {k: row.get(k) for k in ("emp_code","name","email","department","shift_start","shift_end","joined_on")} | {"created_at": epoch_ms(row.get("created_at"))}

def serialize_record(row):
    result = {k: row.get(k) for k in ("emp_code","date","status","work_hours","late_minutes","overtime_minutes","half_day")}
    result["punch_in"] = epoch_ms(row.get("punch_in"))
    result["punch_out"] = epoch_ms(row.get("punch_out"))
    result["history"] = []
    for h in row.get("history") or []:
        changes = {}
        for key, values in (h.get("changes") or {}).items():
            changes[key] = {"from": epoch_ms(values.get("from")) if key in ("punch_in","punch_out") else values.get("from"),
                            "to": epoch_ms(values.get("to")) if key in ("punch_in","punch_out") else values.get("to")}
        result["history"].append({"at": epoch_ms(h.get("at")), "by": h.get("by"), "reason": h.get("reason"), "changes": changes})
    return result

class EmployeeCreate(BaseModel):
    model_config = ConfigDict(extra="ignore")
    emp_code: str = Field(pattern=r"^EMP\d{4,6}$")
    name: str = Field(min_length=1, max_length=100)
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=120)
    department: str = Field(min_length=1, max_length=50)
    shift_start: str = "09:30"
    shift_end: str = "18:30"
    joined_on: date
    @field_validator("shift_start", "shift_end")
    @classmethod
    def valid_shift(cls, v):
        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", v): raise ValueError("Expected HH:MM")
        return v

class PunchInRequest(BaseModel):
    emp_code: str
    punched_at: Optional[int] = None
    status: Literal["PRESENT", "WFH", "ON_DUTY"] = "PRESENT"

class PunchOutRequest(BaseModel):
    emp_code: str
    punched_at: Optional[int] = None

class RegularizeRequest(BaseModel):
    status: Optional[Literal["PRESENT","ABSENT","LEAVE","WFH","ON_DUTY"]] = None
    punch_in: Optional[int] = None
    punch_out: Optional[int] = None
    reason: str = Field(min_length=5, max_length=200)
    regularized_by: str = Field(min_length=1, max_length=50)

#@app.get("/health")
#def health():
 #   try: client.admin.command("ping"); return {"status":"ok"}
  #  except Exception: raise HTTPException(503, "MongoDB unavailable")
@app.get("/health")
def health():
    try: 
        client.admin.command("ping")
        return {"status":"ok", "db": "real"}
    except Exception: 
        return {"status":"ok", "db": "mock-memory"}

@app.post("/employees", status_code=201)
def create_employee(body: EmployeeCreate):
    if body.shift_start == body.shift_end: raise HTTPException(422, "shift_start and shift_end must differ")
    row = body.model_dump()
    row["joined_on"] = row["joined_on"].isoformat()
    row["created_at"] = datetime.now(UTC).replace(microsecond=0)
    try: employees.insert_one(row)
    except DuplicateKeyError: raise HTTPException(409, "Employee code already exists")
    return serialize_employee(row)

@app.get("/employees")
def list_employees(department: Optional[str] = None, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    filt = {"department": department} if department else {}
    total = employees.count_documents(filt)
    rows = employees.find(filt, {"_id":0}).sort("emp_code", ASCENDING).skip((page-1)*page_size).limit(page_size)
    return {"items":[serialize_employee(x) for x in rows], "total":total, "page":page, "page_size":page_size}

@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInRequest):
    emp = employees.find_one({"emp_code":body.emp_code})
    if not emp: raise HTTPException(404, "Employee not found")
    instant = parse_ms(body.punched_at) if body.punched_at is not None else datetime.now(UTC).replace(microsecond=0)
    day = local_date_for_instant(instant, emp)
    if day.isoformat() < emp["joined_on"]: raise HTTPException(422, "Punch-in is before joining date")
    values = derived(instant, None, day, emp)
    row = {"emp_code":body.emp_code, "date":day.isoformat(), "status":body.status, "punch_in":db_dt(instant),
           "punch_out":None, **values, "history":[]}
    try: attendance.insert_one(row)
    except DuplicateKeyError: raise HTTPException(409, "Attendance already exists for this employee and date")
    return serialize_record(row)

@app.post("/attendance/punch-out")
def punch_out(body: PunchOutRequest):
    emp = employees.find_one({"emp_code":body.emp_code})
    if not emp: raise HTTPException(404, "Employee not found")
    instant = parse_ms(body.punched_at) if body.punched_at is not None else datetime.now(UTC).replace(microsecond=0)
    instant_db = db_dt(instant)
    row = attendance.find_one({"emp_code":body.emp_code, "punch_in":{"$lte":instant_db}}, sort=[("punch_in",DESCENDING)])
    if not row: raise HTTPException(404, "No punch-in found")
    if row.get("punch_out") is not None: raise HTTPException(409, "Already punched out")
    pin = utc_aware(row["punch_in"])
    if instant <= pin or instant - pin > timedelta(hours=24): raise HTTPException(422, "Punch-out must be after punch-in and within 24 hours")
    day = date.fromisoformat(row["date"])
    fields = derived(pin, instant, day, emp)
    updated = attendance.find_one_and_update({"emp_code":body.emp_code,"date":row["date"],"punch_out":None},
        {"$set":{"punch_out":instant_db,**fields}},return_document=ReturnDocument.AFTER)
    if not updated: raise HTTPException(409, "Concurrent punch-out already completed")
    return serialize_record(updated)

@app.get("/attendance")
def list_attendance(emp_code: Optional[str] = None, date_from: Optional[str] = None, date_to: Optional[str] = None,
                    status: Optional[str] = None, page: int = Query(1,ge=1), page_size: int = Query(20,ge=1,le=100)):
    filt = {}
    if emp_code: filt["emp_code"] = emp_code
    if status:
        if status not in STATUSES: raise HTTPException(422, "Invalid status")
        filt["status"] = status
    if date_from or date_to:
        filt["date"] = {}
        if date_from: filt["date"]["$gte"] = parse_date(date_from).isoformat()
        if date_to: filt["date"]["$lte"] = parse_date(date_to).isoformat()
        if date_from and date_to and date_from > date_to: raise HTTPException(422, "date_from must be <= date_to")
    total = attendance.count_documents(filt)
    rows = attendance.find(filt,{"_id":0}).sort([("date",DESCENDING),("emp_code",ASCENDING)]).skip((page-1)*page_size).limit(page_size)
    return {"items":[serialize_record(x) for x in rows],"total":total,"page":page,"page_size":page_size}

@app.patch("/attendance/{emp_code}/{day}")
def regularize(emp_code: str, day: str, body: RegularizeRequest):
    parsed_day = parse_date(day)
    emp = employees.find_one({"emp_code":emp_code})
    if not emp: raise HTTPException(404, "Employee not found")
    row = attendance.find_one({"emp_code":emp_code,"date":parsed_day.isoformat()})
    if not row: raise HTTPException(404, "Attendance record not found")
    supplied = body.model_fields_set
    if body.status in ("ABSENT","LEAVE") and (("punch_in" in supplied) or ("punch_out" in supplied)):
        raise HTTPException(422, "ABSENT/LEAVE cannot be supplied with punch times")
    status = body.status if body.status is not None else row["status"]
    pin = parse_ms(body.punch_in) if "punch_in" in supplied and body.punch_in is not None else (None if "punch_in" in supplied else utc_aware(row.get("punch_in")))
    pout = parse_ms(body.punch_out) if "punch_out" in supplied and body.punch_out is not None else (None if "punch_out" in supplied else utc_aware(row.get("punch_out")))
    if status in ("ABSENT","LEAVE"): pin=pout=None
    if status in PRESENCE and pin is None: raise HTTPException(422, "Presence status requires punch_in")
    if pin and local_date_for_instant(pin,emp).isoformat() != parsed_day.isoformat(): raise HTTPException(422, "punch_in must belong to attendance date")
    if pout and (not pin or pout <= pin or pout-pin > timedelta(hours=24)): raise HTTPException(422, "Invalid punch_out")
    calc = derived(pin,pout,parsed_day,emp)
    newvals = {"status":status,"punch_in":db_dt(pin),"punch_out":db_dt(pout),**calc}
    changes={}
    for k,v in newvals.items():
        old=row.get(k)
        if k in ("punch_in","punch_out"):
            old=utc_aware(old); v=utc_aware(v)
        if old != v:
            changes[k]={"from":row.get(k),"to":db_dt(v) if k in ("punch_in","punch_out") else v}
    if not changes: raise HTTPException(422, "Request does not change the record")
    history_entry={"at":datetime.now(UTC).replace(microsecond=0),"by":body.regularized_by,"reason":body.reason,"changes":changes}
    updated=attendance.find_one_and_update({"emp_code":emp_code,"date":parsed_day.isoformat(),"history":row.get("history",[])},
        {"$set":newvals,"$push":{"history":history_entry}},return_document=ReturnDocument.AFTER)
    if not updated: raise HTTPException(409,"Record changed concurrently; retry")
    return serialize_record(updated)

def month_filter(month):
    start,end=month_bounds(month)
    return start,end,{"date":{"$gte":start.isoformat(),"$lte":end.isoformat()}}

@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(emp_code: str, month: str):
    emp=employees.find_one({"emp_code":emp_code})
    if not emp: raise HTTPException(404,"Employee not found")
    start,end,filt=month_filter(month)
    joined=max(start,date.fromisoformat(emp["joined_on"]))
    wd=working_days(joined,end)
    pipeline=[{"$match":{"emp_code":emp_code,**filt}},{"$group":{"_id":None,
        "present_days":{"$sum":{"$cond":[{"$and":[{"$in":["$status",list(PRESENCE)]},{"$lt":[{"$dayOfWeek":{"$dateFromString":{"dateString":"$date"}}},6]}]}, {"$cond":[{"$eq":["$half_day",True]},0.5,1]},0]}},
        "leave_days":{"$sum":{"$cond":[{"$eq":["$status","LEAVE"]},1,0]}},
        "late_count":{"$sum":{"$cond":[{"$gt":["$late_minutes",0]},1,0]}},
        "total_late_minutes":{"$sum":"$late_minutes"},"total_overtime_minutes":{"$sum":"$overtime_minutes"}}}]
    result=list(attendance.aggregate(pipeline))
    r=result[0] if result else {}
    present=round_half_up(r.get("present_days",0),2)
    return {"emp_code":emp_code,"month":month,"working_days":wd,"present_days":present,"leave_days":r.get("leave_days",0),
        "late_count":r.get("late_count",0),"total_late_minutes":r.get("total_late_minutes",0),
        "total_overtime_minutes":r.get("total_overtime_minutes",0),"attendance_pct":round_half_up(present/wd*100,2) if wd else None}

@app.get("/analytics/departments/summary")
def department_summary(month: str, department: Optional[str] = None):
    start,end=month_bounds(month)
    emp_match={"joined_on":{"$lte":end.isoformat()}}
    if department: emp_match["department"]=department
    pipeline=[{"$match":emp_match},{"$lookup":{"from":"attendance","let":{"code":"$emp_code"},"pipeline":[
        {"$match":{"$expr":{"$and":[{"$eq":["$emp_code","$$code"]},{"$gte":["$date",start.isoformat()]},{"$lte":["$date",end.isoformat()]}]}}},
        {"$group":{"_id":None,"present_days":{"$sum":{"$cond":[{"$and":[{"$in":["$status",list(PRESENCE)]},{"$lt":[{"$dayOfWeek":{"$dateFromString":{"dateString":"$date"}}},6]}]},{"$cond":[{"$eq":["$half_day",True]},0.5,1]},0]}},
          "hours_sum":{"$sum":{"$cond":[{"$and":[{"$in":["$status",list(PRESENCE)]},{"$ne":["$work_hours",None]}]},"$work_hours",0]}},
          "hours_count":{"$sum":{"$cond":[{"$and":[{"$in":["$status",list(PRESENCE)]},{"$ne":["$work_hours",None]}]},1,0]}},
          "late_count":{"$sum":{"$cond":[{"$gt":["$late_minutes",0]},1,0]}},
          "total_late_minutes":{"$sum":"$late_minutes"},"leave_count":{"$sum":{"$cond":[{"$eq":["$status","LEAVE"]},1,0]}},
          "on_duty_count":{"$sum":{"$cond":[{"$eq":["$status","ON_DUTY"]},1,0]}}}}],"as":"stats"}},
        {"$unwind":{"path":"$stats","preserveNullAndEmptyArrays":True}},
        {"$group":{"_id":"$department","headcount":{"$sum":1},"present_days":{"$sum":{"$ifNull":["$stats.present_days",0]}},
          "hours_sum":{"$sum":{"$ifNull":["$stats.hours_sum",0]}},"hours_count":{"$sum":{"$ifNull":["$stats.hours_count",0]}},
          "late_count":{"$sum":{"$ifNull":["$stats.late_count",0]}},"total_late_minutes":{"$sum":{"$ifNull":["$stats.total_late_minutes",0]}},
          "leave_count":{"$sum":{"$ifNull":["$stats.leave_count",0]}},"on_duty_count":{"$sum":{"$ifNull":["$stats.on_duty_count",0]}}}},
        {"$sort":{"_id":1}}]
    rows=list(employees.aggregate(pipeline))
    items=[]
    for r in rows:
        if not r["headcount"]: continue
        items.append({"department":r["_id"],"headcount":r["headcount"],"present_days":round_half_up(r["present_days"],2),
          "avg_work_hours":round_half_up(r["hours_sum"]/r["hours_count"],2) if r["hours_count"] else None,
          "late_count":r["late_count"],"total_late_minutes":r["total_late_minutes"],"leave_count":r["leave_count"],"on_duty_count":r["on_duty_count"]})
    return {"month":month,"items":items}

@app.get("/analytics/leaderboard/late")
def late_leaderboard(month: str, limit: int=Query(10,ge=1,le=50), department: Optional[str]=None):
    start,end,filt=month_filter(month)
    pipeline=[{"$match":filt},{"$group":{"_id":"$emp_code","total_late_minutes":{"$sum":"$late_minutes"},"late_count":{"$sum":{"$cond":[{"$gt":["$late_minutes",0]},1,0]}}}},
      {"$match":{"total_late_minutes":{"$gt":0}}},{"$lookup":{"from":"employees","localField":"_id","foreignField":"emp_code","as":"employee"}},
      {"$unwind":"$employee"}]
    if department: pipeline.append({"$match":{"employee.department":department}})
    pipeline += [{"$setWindowFields":{"sortBy":{"total_late_minutes":-1},"output":{"rank":{"$rank":{}}}}},
      {"$match":{"rank":{"$lte":limit}}},{"$sort":{"total_late_minutes":-1,"_id":1}},
      {"$project":{"_id":0,"rank":1,"emp_code":"$_id","name":"$employee.name","department":"$employee.department","total_late_minutes":1,"late_count":1}}]
    return {"month":month,"items":list(attendance.aggregate(pipeline))}

@app.get("/analytics/departments/{department}/trend")
def department_trend(department: str, from_date: str=Query(alias="from"), to_date: str=Query(alias="to")):
    start=parse_date(from_date); end=parse_date(to_date)
    if end < start or (end-start).days > 91: raise HTTPException(422,"Date range must be ordered and at most 92 days")
    if not employees.find_one({"department":department}): raise HTTPException(404,"Unknown department")
    start_dt=datetime.combine(start,time.min,TZ).astimezone(UTC).replace(tzinfo=None)
    end_dt=datetime.combine(end+timedelta(days=1),time.min,TZ).astimezone(UTC).replace(tzinfo=None)
    pipeline=[{"$match":{"date":{"$gte":start.isoformat(),"$lte":end.isoformat()}}},
      {"$lookup":{"from":"employees","localField":"emp_code","foreignField":"emp_code","as":"employee"}},
      {"$unwind":"$employee"},{"$match":{"employee.department":department}},
      {"$group":{"_id":"$date","present_count":{"$sum":{"$cond":[{"$in":["$status",list(PRESENCE)]},{"$cond":[{"$eq":["$half_day",True]},0.5,1]},0]}},
        "late_count":{"$sum":{"$cond":[{"$gt":["$late_minutes",0]},1,0]}}}},
      {"$project":{"date":{"$dateFromString":{"dateString":"$_id"}},"present_count":1,"late_count":1}},
      {"$densify":{"field":"date","range":{"step":1,"unit":"day","bounds":[{"$dateFromString":{"dateString":start.isoformat()}},{"$dateFromString":{"dateString":(end+timedelta(days=1)).isoformat()}}]}}},
      {"$set":{"date_str":{"$dateToString":{"date":"$date","format":"%Y-%m-%d"}}}},
      {"$lookup":{"from":"employees","let":{"d":"$date_str"},"pipeline":[{"$match":{"$expr":{"$and":[{"$eq":["$department",department]},{"$lte":["$joined_on","$$d"]}]}}},{"$count":"n"}],"as":"hc"}},
      {"$set":{"headcount":{"$ifNull":[{"$arrayElemAt":["$hc.n",0]},0]},"present_count":{"$ifNull":["$present_count",0]},"late_count":{"$ifNull":["$late_count",0]},
        "is_working_day":{"$lt":[{"$dayOfWeek":"$date"},7]}}},
      {"$set":{"is_working_day":{"$and":[{"$gte":[{"$dayOfWeek":"$date"},2]},{"$lte":[{"$dayOfWeek":"$date"},6]}]}}},
      {"$set":{"attendance_rate":{"$cond":[{"$and":["$is_working_day",{"$gt":["$headcount",0]}]},{"$round":[{"$divide":["$present_count","$headcount"]},4]},None]}}},
      {"$setWindowFields":{"sortBy":{"date":1},"output":{"moving_avg_7d":{"$avg":"$attendance_rate","window":{"documents":[-6,0]}}}}},
      {"$sort":{"date":1}},{"$project":{"_id":0,"date":{"$dateToString":{"date":"$date","format":"%Y-%m-%d"}},"is_working_day":1,"headcount":1,"present_count":1,"late_count":1,"attendance_rate":1,"moving_avg_7d":{"$round":["$moving_avg_7d",4]}}}]
    return {"department":department,"items":list(attendance.aggregate(pipeline))}

@app.get("/admin/explain/{endpoint}")
def explain_endpoint(endpoint: str, emp_code: Optional[str]=None, month: Optional[str]=None,
                     department: Optional[str]=None, limit: int=Query(10,ge=1,le=50),
                     date_from: Optional[str]=None,date_to: Optional[str]=None,status: Optional[str]=None,
                     page: int=Query(1,ge=1),page_size: int=Query(20,ge=1,le=100),
                     from_date: Optional[str]=Query(None,alias="from"),to_date: Optional[str]=Query(None,alias="to")):
    allowed={"attendance_list","employee_monthly","department_summary","late_leaderboard","department_trend"}
    if endpoint not in allowed: raise HTTPException(422,"Unsupported endpoint")
    if endpoint=="attendance_list":
        filt={}
        if emp_code: filt["emp_code"]=emp_code
        if status: filt["status"]=status
        if date_from or date_to:
            filt["date"]={}
            if date_from: filt["date"]["$gte"]=parse_date(date_from).isoformat()
            if date_to: filt["date"]["$lte"]=parse_date(date_to).isoformat()
        query=attendance.find(filt).sort([("date",-1),("emp_code",1)]).skip((page-1)*page_size).limit(page_size)
        raw=attendance.database.command("explain",{"find":"attendance","filter":filt,"sort":{"date":-1,"emp_code":1},"skip":(page-1)*page_size,"limit":page_size}, verbosity="executionStats")
        return {"endpoint":endpoint,"collection":"attendance","explain":raw}
    if not month: raise HTTPException(422,"month is required")
    start,end=month_bounds(month)
    if endpoint=="employee_monthly":
        if not emp_code: raise HTTPException(422,"emp_code is required")
        filt={"emp_code":emp_code,"date":{"$gte":start.isoformat(),"$lte":end.isoformat()}}
        raw=attendance.database.command("explain",{"aggregate":"attendance","pipeline":[{"$match":filt},{"$group":{"_id":"$emp_code","late":{"$sum":"$late_minutes"}}}],"cursor":{}}, verbosity="executionStats")
        return {"endpoint":endpoint,"collection":"attendance","explain":raw}
    if endpoint=="department_summary":
        raw=employees.database.command("explain",{"aggregate":"employees","pipeline":[{"$match":{"joined_on":{"$lte":end.isoformat()},**({"department":department} if department else {})}},{"$lookup":{"from":"attendance","localField":"emp_code","foreignField":"emp_code","as":"logs"}}],"cursor":{}}, verbosity="executionStats")
        return {"endpoint":endpoint,"collection":"employees","explain":raw}
    if endpoint=="late_leaderboard":
        raw=attendance.database.command("explain",{"aggregate":"attendance","pipeline":[{"$match":{"date":{"$gte":start.isoformat(),"$lte":end.isoformat()}}},{"$group":{"_id":"$emp_code","total":{"$sum":"$late_minutes"}}}],"cursor":{}}, verbosity="executionStats")
        return {"endpoint":endpoint,"collection":"attendance","explain":raw}
    if not department or not from_date or not to_date: raise HTTPException(422,"department, from and to are required")
    parse_date(from_date); parse_date(to_date)
    raw=attendance.database.command("explain",{"aggregate":"attendance","pipeline":[{"$match":{"date":{"$gte":from_date,"$lte":to_date}}}],"cursor":{}}, verbosity="executionStats")
    return {"endpoint":endpoint,"collection":"attendance","explain":raw}
