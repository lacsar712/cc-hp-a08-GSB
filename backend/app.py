import json
import os
from datetime import datetime, timedelta, timezone

import psycopg
from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel, Field
from psycopg.rows import dict_row

from rules import judge

SECRET = os.environ.get("JWT_SECRET", "herb-process-dev-secret")
DSN = os.environ.get("DATABASE_URL", "postgresql://app:app@localhost:54393/herb")
CN_TZ = timezone(timedelta(hours=8))  # 自然日按北京时间划分
pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
security = HTTPBearer(auto_error=False)
USERS = {
    "processor": {"role": "writer", "password_hash": pwd.hash("herb123456")},
    "checker": {"role": "reader", "password_hash": pwd.hash("check123456")},
}


def connect():
    return psycopg.connect(DSN, row_factory=dict_row)


def today_bounds() -> tuple[datetime, datetime]:
    """当日自然日 [起, 止)，按北京时间。"""
    now = datetime.now(CN_TZ)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=1)


class LoginIn(BaseModel):
    username: str
    password: str


class StepIn(BaseModel):
    name: str
    temp_c: float
    minutes: float


class BatchIn(BaseModel):
    herb: str = Field(min_length=1, max_length=80)
    steps: list[StepIn]


class PairIn(BaseModel):
    herb_a: str = Field(min_length=1, max_length=80)
    herb_b: str = Field(min_length=1, max_length=80)


def current_user(credentials: HTTPAuthorizationCredentials | None = Depends(security)) -> dict:
    if credentials is None:
        raise HTTPException(status_code=401, detail="未登录")
    try:
        payload = jwt.decode(credentials.credentials, SECRET, algorithms=["HS256"])
    except JWTError as exc:
        raise HTTPException(status_code=401, detail="无效令牌") from exc
    if payload.get("sub") not in USERS:
        raise HTTPException(status_code=401, detail="无效令牌")
    return {"username": payload["sub"], "role": payload.get("role")}


def require_writer(user: dict = Depends(current_user)) -> dict:
    if user["role"] != "writer":
        raise HTTPException(status_code=403, detail="仅炮制员可写入")
    return user


app = FastAPI(title="饮片炮制记录台")


@app.on_event("startup")
def startup():
    with connect() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS batches (
                id serial PRIMARY KEY,
                herb text NOT NULL,
                doc jsonb NOT NULL,
                verdict text NOT NULL,
                reason text NOT NULL,
                created_by text NOT NULL,
                created_at timestamptz NOT NULL
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS incompat_pairs (
                id serial PRIMARY KEY,
                herb_a text NOT NULL,
                herb_b text NOT NULL,
                created_by text NOT NULL,
                created_at timestamptz NOT NULL
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS incompat_events (
                id serial PRIMARY KEY,
                action text NOT NULL,
                herb_a text NOT NULL,
                herb_b text NOT NULL,
                actor text NOT NULL,
                created_at timestamptz NOT NULL
            )"""
        )
        count = conn.execute("SELECT COUNT(*) AS n FROM batches").fetchone()["n"]
        if count == 0:
            # 种子数据落在昨日，避免触发当日配伍禁忌拦截
            now = datetime.now(timezone.utc) - timedelta(days=1)
            samples = [
                ("甘草", {"steps": [{"name": "清炒", "temp_c": 120, "minutes": 12}]}),
                ("黄芩", {"steps": [{"name": "清炒", "temp_c": 40, "minutes": 12}]}),
            ]
            for herb, doc in samples:
                verdict, reason = judge(doc)
                conn.execute(
                    """INSERT INTO batches (herb, doc, verdict, reason, created_by, created_at)
                       VALUES (%s, %s::jsonb, %s, %s, %s, %s)""",
                    (herb, json.dumps(doc, ensure_ascii=False), verdict, reason, "processor", now),
                )
        conn.commit()


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "herb-process-record"}


@app.post("/api/auth/login")
def login(body: LoginIn):
    user = USERS.get(body.username.strip())
    if not user or not pwd.verify(body.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    exp = datetime.now(timezone.utc) + timedelta(hours=8)
    token = jwt.encode({"sub": body.username.strip(), "role": user["role"], "exp": exp}, SECRET, algorithm="HS256")
    return {"access_token": token, "username": body.username.strip(), "role": user["role"]}


@app.get("/api/batches")
def list_batches(_user: dict = Depends(current_user)):
    with connect() as conn:
        rows = conn.execute("SELECT id, herb, doc, verdict, reason, created_by FROM batches ORDER BY id DESC").fetchall()
    return rows


@app.post("/api/batches", status_code=201)
def create_batch(body: BatchIn, user: dict = Depends(require_writer)):
    herb = body.herb.strip()
    doc = {"steps": [s.model_dump() for s in body.steps]}
    verdict, reason = judge(doc)
    start, end = today_bounds()
    with connect() as conn:
        # 配伍禁忌：本日已有冲突味（放行或未放行均算）的成功写入，则拒写
        conflicts = conn.execute(
            """SELECT p.herb_a, p.herb_b FROM incompat_pairs p
               WHERE (p.herb_a = %s AND EXISTS(SELECT 1 FROM batches b
                                               WHERE b.herb = p.herb_b AND b.created_at >= %s AND b.created_at < %s))
                  OR (p.herb_b = %s AND EXISTS(SELECT 1 FROM batches b
                                               WHERE b.herb = p.herb_a AND b.created_at >= %s AND b.created_at < %s))""",
            (herb, start, end, herb, start, end),
        ).fetchall()
        if conflicts:
            others = sorted({c["herb_b"] if c["herb_a"] == herb else c["herb_a"] for c in conflicts})
            names = "、".join(f"「{n}」" for n in others)
            raise HTTPException(status_code=409, detail=f"配伍禁忌：本日已有{names}的炮制记录，与「{herb}」互为禁忌，拒绝写入")
        row = conn.execute(
            """INSERT INTO batches (herb, doc, verdict, reason, created_by, created_at)
               VALUES (%s, %s::jsonb, %s, %s, %s, %s)
               RETURNING id, herb, doc, verdict, reason, created_by""",
            (herb, json.dumps(doc, ensure_ascii=False), verdict, reason, user["username"], datetime.now(timezone.utc)),
        ).fetchone()
        conn.commit()
    return row


@app.get("/api/incompat/pairs")
def list_pairs(_user: dict = Depends(current_user)):
    start, end = today_bounds()
    with connect() as conn:
        rows = conn.execute(
            """SELECT p.id, p.herb_a, p.herb_b, p.created_by, p.created_at,
                      EXISTS(SELECT 1 FROM batches b
                             WHERE b.herb = p.herb_a AND b.created_at >= %s AND b.created_at < %s) AS a_today,
                      EXISTS(SELECT 1 FROM batches b
                             WHERE b.herb = p.herb_b AND b.created_at >= %s AND b.created_at < %s) AS b_today
               FROM incompat_pairs p ORDER BY p.id DESC""",
            (start, end, start, end),
        ).fetchall()
    return rows


@app.post("/api/incompat/pairs", status_code=201)
def create_pair(body: PairIn, user: dict = Depends(require_writer)):
    a, b = body.herb_a.strip(), body.herb_b.strip()
    if a == b:
        raise HTTPException(status_code=400, detail="同一味饮片不能与自己结为禁忌对")
    now = datetime.now(timezone.utc)
    with connect() as conn:
        dup = conn.execute(
            """SELECT 1 FROM incompat_pairs
               WHERE (herb_a = %s AND herb_b = %s) OR (herb_a = %s AND herb_b = %s)""",
            (a, b, b, a),
        ).fetchone()
        if dup:
            raise HTTPException(status_code=409, detail=f"「{a}」与「{b}」的禁忌对已存在")
        row = conn.execute(
            """INSERT INTO incompat_pairs (herb_a, herb_b, created_by, created_at)
               VALUES (%s, %s, %s, %s)
               RETURNING id, herb_a, herb_b, created_by, created_at""",
            (a, b, user["username"], now),
        ).fetchone()
        conn.execute(
            """INSERT INTO incompat_events (action, herb_a, herb_b, actor, created_at)
               VALUES (%s, %s, %s, %s, %s)""",
            ("add", a, b, user["username"], now),
        )
        conn.commit()
    return row


@app.delete("/api/incompat/pairs/{pair_id}")
def delete_pair(pair_id: int, user: dict = Depends(require_writer)):
    now = datetime.now(timezone.utc)
    with connect() as conn:
        row = conn.execute("SELECT id, herb_a, herb_b FROM incompat_pairs WHERE id = %s", (pair_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="禁忌对不存在")
        conn.execute("DELETE FROM incompat_pairs WHERE id = %s", (pair_id,))
        conn.execute(
            """INSERT INTO incompat_events (action, herb_a, herb_b, actor, created_at)
               VALUES (%s, %s, %s, %s, %s)""",
            ("remove", row["herb_a"], row["herb_b"], user["username"], now),
        )
        conn.commit()
    return {"ok": True, "removed": row}


@app.get("/api/incompat/events")
def list_events(_user: dict = Depends(current_user)):
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, action, herb_a, herb_b, actor, created_at FROM incompat_events ORDER BY id DESC"
        ).fetchall()
    return rows
