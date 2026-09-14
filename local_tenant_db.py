"""Local SQLite organization, branch, onboarding, and session metadata."""
from __future__ import annotations
import json, os, sqlite3, threading, uuid
from datetime import datetime, timezone
from typing import Any
DB_PATH = os.environ.get("ATTENDANCE_SQLITE_PATH", os.path.join("data", "attendance.db"))
_local = threading.local(); _lock = threading.RLock()
def _connect():
    conn=getattr(_local,"conn",None)
    if conn is not None:return conn
    os.makedirs(os.path.dirname(DB_PATH) or ".",exist_ok=True)
    conn=sqlite3.connect(DB_PATH,timeout=30,check_same_thread=False); conn.row_factory=sqlite3.Row
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS local_organizations (id TEXT PRIMARY KEY,name TEXT, status TEXT DEFAULT 'active', payload_json TEXT DEFAULT '{}', updated_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS local_branches (id TEXT PRIMARY KEY,org_id TEXT NOT NULL,name TEXT NOT NULL,location TEXT,max_staff_capacity INTEGER DEFAULT 100000,timezone TEXT DEFAULT 'UTC',fallback_active INTEGER DEFAULT 0,dropped_at TEXT,payload_json TEXT DEFAULT '{}',created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS local_onboarding (org_id TEXT PRIMARY KEY,completed_by TEXT,config_json TEXT NOT NULL,updated_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS local_sessions (account_type TEXT NOT NULL,user_id TEXT NOT NULL,session_id TEXT,reason TEXT,updated_at TEXT NOT NULL,PRIMARY KEY(account_type,user_id));
    """);conn.commit();_local.conn=conn;return conn
def _now():return datetime.now(timezone.utc).isoformat()
def save_onboarding(org_id,user_id,config):
    with _lock:
        c=_connect();c.execute("INSERT INTO local_onboarding(org_id,completed_by,config_json,updated_at) VALUES(?,?,?,?) ON CONFLICT(org_id) DO UPDATE SET completed_by=excluded.completed_by,config_json=excluded.config_json,updated_at=excluded.updated_at",(str(org_id),str(user_id),json.dumps(config,default=str),_now()));c.commit()
    return {
        "onboarding": config,
        "config": config,
        "camera_sync": {"synced_branches": len(config.get("cameras") or {}), "synced_cameras": sum(len(v or []) for v in (config.get("cameras") or {}).values()) if isinstance(config.get("cameras"), dict) else 0},
        "cameraSync": {"synced_branches": len(config.get("cameras") or {}), "synced_cameras": sum(len(v or []) for v in (config.get("cameras") or {}).values()) if isinstance(config.get("cameras"), dict) else 0},
    }
def get_onboarding(org_id):
    row=_connect().execute("SELECT config_json FROM local_onboarding WHERE org_id=?",(str(org_id),)).fetchone();return json.loads(row["config_json"]) if row else None
def add_department(org_id,user_id,branch_id,name):
    """Append a single workforce department (Settings.tsx's GroupItem shape)
    to this org's onboarding config, touching nothing else in the blob.

    Runs the whole read-modify-write under `_lock` (the same lock
    save_onboarding takes) so a concurrent full Settings.tsx save can never
    interleave with this and silently drop the addition, or vice versa.
    `_lock` is an RLock, so re-entering it via the save_onboarding() call
    below (same thread) is safe.
    """
    clean_name=(name or "").strip()
    if not clean_name:raise ValueError("Department name is required")
    with _lock:
        config=get_onboarding(org_id) or {}
        departments=config.setdefault("departments",{})
        branch_key=str(branch_id)
        branch_departments=departments.setdefault(branch_key,[])
        if any((item.get("name") or "").strip().lower()==clean_name.lower() for item in branch_departments):
            raise ValueError(f'"{clean_name}" is already configured for this branch.')
        new_department={"id":f"department_{uuid.uuid4().hex[:12]}","name":clean_name,"itemKind":"group","personFamily":"workforce"}
        branch_departments.append(new_department)
        save_onboarding(org_id,user_id,config)
    return new_department,branch_departments
def add_designation(org_id,user_id,branch_id,department_id,name):
    """Append a single workforce designation (Settings.tsx's DesignationItem
    shape) under an existing department. Same locked read-modify-write as
    add_department; validates the department belongs to this branch before
    attaching to it."""
    clean_name=(name or "").strip()
    if not clean_name:raise ValueError("Designation name is required")
    if not department_id:raise ValueError("department_id is required")
    with _lock:
        config=get_onboarding(org_id) or {}
        branch_key=str(branch_id)
        branch_departments=(config.get("departments") or {}).get(branch_key,[])
        if not any(item.get("id")==department_id for item in branch_departments):
            raise ValueError("Department not found for this branch.")
        roles=config.setdefault("roles",{})
        branch_roles=roles.setdefault(branch_key,[])
        new_designation={"id":f"designation_{uuid.uuid4().hex[:12]}","name":clean_name,"departmentId":department_id,"level":"custom","personFamily":"workforce"}
        branch_roles.append(new_designation)
        save_onboarding(org_id,user_id,config)
    return new_designation,[item for item in branch_roles if item.get("departmentId")==department_id]
def list_branches(org_id,include_dropped=False):
    sql="SELECT * FROM local_branches WHERE org_id=?";p=[str(org_id)]
    if not include_dropped:sql+=" AND dropped_at IS NULL"
    return [dict(r) for r in _connect().execute(sql+" ORDER BY created_at",p).fetchall()]
def create_branch(payload):
    now=_now();row={"id":str(uuid.uuid4()),"org_id":str(payload["org_id"]),"name":str(payload.get("name") or "").strip(),"location":payload.get("location"),"max_staff_capacity":int(payload.get("max_staff_capacity") or 50),"timezone":payload.get("timezone") or "UTC","fallback_active":int(bool(payload.get("fallback_active",False))),"dropped_at":None,"payload_json":json.dumps(payload,default=str),"created_at":now,"updated_at":now}
    if not row["name"]:raise ValueError("Branch name is required")
    with _lock:
        c=_connect();c.execute("INSERT INTO local_branches (id,org_id,name,location,max_staff_capacity,timezone,fallback_active,dropped_at,payload_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",tuple(row.values()));c.commit()
    return row
def update_branch(branch_id,payload,org_id=None):
    sql="SELECT * FROM local_branches WHERE id=?";p=[str(branch_id)]
    if org_id:sql+=" AND org_id=?";p.append(str(org_id))
    row=_connect().execute(sql,p).fetchone()
    if not row:raise ValueError("Branch not found")
    allowed={k:v for k,v in payload.items() if k in {"name","location","max_staff_capacity","fallback_active","timezone"}}
    if not allowed:raise ValueError("No valid branch fields to update")
    sets=", ".join(f"{k}=?" for k in allowed);vals=list(allowed.values())+[_now(),str(branch_id)]
    with _lock:
        c=_connect();c.execute(f"UPDATE local_branches SET {sets},updated_at=? WHERE id=?",vals);c.commit()
    return dict(c.execute("SELECT * FROM local_branches WHERE id=?",(str(branch_id),)).fetchone())
def rotate_session(account_type,user_id,session_id,reason):
    with _lock:
        c=_connect();c.execute("INSERT INTO local_sessions(account_type,user_id,session_id,reason,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(account_type,user_id) DO UPDATE SET session_id=excluded.session_id,reason=excluded.reason,updated_at=excluded.updated_at",(account_type,str(user_id),session_id,reason,_now()));c.commit()
def session(account_type,user_id):
    row=_connect().execute("SELECT session_id,reason FROM local_sessions WHERE account_type=? AND user_id=?",(account_type,str(user_id))).fetchone();return dict(row) if row else None