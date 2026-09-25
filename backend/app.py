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
# 自然日按中国标准时间（UTC+8，无夏令时）划分
CN_TZ = timezone(timedelta(hours=8), "CST")
pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
security = HTTPBearer(auto_error=False)
USERS = {
    "processor": {"role": "writer", "password_hash": pwd.hash("herb123456")},
    "checker": {"role": "reader", "password_hash": pwd.hash("check123456")},
}


def connect():
    return psycopg.connect(DSN, row_factory=dict_row)


def day_bounds(now: datetime | None = None) -> tuple[datetime, datetime]:
    """当前自然日（UTC+8）的起止时刻，用于“同一自然日”判定。"""
    local = (now or datetime.now(CN_TZ)).astimezone(CN_TZ)
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
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
        raise HTTPException(status_code=403, detail="仅炮制员可写入记录")
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
                created_at timestamptz NOT NULL,
                UNIQUE (herb_a, herb_b)
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS incompat_history (
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
            # 样例数据记为前一日，避免干扰当日的配伍禁忌判定
            seed_time = datetime.now(timezone.utc) - timedelta(days=1)
            samples = [
                ("甘草", {"steps": [{"name": "清炒", "temp_c": 120, "minutes": 12}]}),
                ("黄芩", {"steps": [{"name": "清炒", "temp_c": 40, "minutes": 12}]}),
            ]
            for herb, doc in samples:
                verdict, reason = judge(doc)
                conn.execute(
                    """INSERT INTO batches (herb, doc, verdict, reason, created_by, created_at)
                       VALUES (%s, %s::jsonb, %s, %s, %s, %s)""",
                    (herb, json.dumps(doc, ensure_ascii=False), verdict, reason, "processor", seed_time),
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
    if not herb:
        raise HTTPException(status_code=400, detail="饮片名不能为空")
    doc = {"steps": [s.model_dump() for s in body.steps]}
    verdict, reason = judge(doc)
    start, end = day_bounds()
    with connect() as conn:
        pairs = conn.execute(
            "SELECT herb_a, herb_b FROM incompat_pairs WHERE herb_a = %s OR herb_b = %s",
            (herb, herb),
        ).fetchall()
        for pair in pairs:
            other = pair["herb_b"] if pair["herb_a"] == herb else pair["herb_a"]
            hit = conn.execute(
                """SELECT 1 FROM batches
                   WHERE herb = %s AND created_at >= %s AND created_at < %s
                   LIMIT 1""",
                (other, start, end),
            ).fetchone()
            if hit:
                raise HTTPException(
                    status_code=409,
                    detail=f"配伍禁忌：本日已有「{other}」的炮制记录，「{herb}」与「{other}」不得在同一自然日都写入",
                )
        row = conn.execute(
            """INSERT INTO batches (herb, doc, verdict, reason, created_by, created_at)
               VALUES (%s, %s::jsonb, %s, %s, %s, %s)
               RETURNING id, herb, doc, verdict, reason, created_by""",
            (herb, json.dumps(doc, ensure_ascii=False), verdict, reason, user["username"], datetime.now(timezone.utc)),
        ).fetchone()
        conn.commit()
    return row


def normalize_pair(herb_a: str, herb_b: str) -> tuple[str, str]:
    a, b = herb_a.strip(), herb_b.strip()
    if not a or not b:
        raise HTTPException(status_code=400, detail="药味名不能为空")
    if a == b:
        raise HTTPException(status_code=400, detail="同一味药不能与自己组成禁忌对")
    return (a, b) if a < b else (b, a)


@app.get("/api/incompat/pairs")
def list_pairs(_user: dict = Depends(current_user)):
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, herb_a, herb_b, created_by, created_at FROM incompat_pairs ORDER BY id DESC"
        ).fetchall()
    return rows


@app.post("/api/incompat/pairs", status_code=201)
def add_pair(body: PairIn, user: dict = Depends(require_writer)):
    a, b = normalize_pair(body.herb_a, body.herb_b)
    now = datetime.now(timezone.utc)
    with connect() as conn:
        dup = conn.execute(
            "SELECT 1 FROM incompat_pairs WHERE herb_a = %s AND herb_b = %s", (a, b)
        ).fetchone()
        if dup:
            raise HTTPException(status_code=409, detail=f"「{a}」与「{b}」已在禁忌表中")
        row = conn.execute(
            """INSERT INTO incompat_pairs (herb_a, herb_b, created_by, created_at)
               VALUES (%s, %s, %s, %s)
               RETURNING id, herb_a, herb_b, created_by, created_at""",
            (a, b, user["username"], now),
        ).fetchone()
        conn.execute(
            """INSERT INTO incompat_history (action, herb_a, herb_b, actor, created_at)
               VALUES ('add', %s, %s, %s, %s)""",
            (a, b, user["username"], now),
        )
        conn.commit()
    return row


@app.delete("/api/incompat/pairs/{pair_id}")
def remove_pair(pair_id: int, user: dict = Depends(require_writer)):
    now = datetime.now(timezone.utc)
    with connect() as conn:
        row = conn.execute(
            "DELETE FROM incompat_pairs WHERE id = %s RETURNING herb_a, herb_b", (pair_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="禁忌对不存在")
        conn.execute(
            """INSERT INTO incompat_history (action, herb_a, herb_b, actor, created_at)
               VALUES ('remove', %s, %s, %s, %s)""",
            (row["herb_a"], row["herb_b"], user["username"], now),
        )
        conn.commit()
    return {"removed": pair_id, "herb_a": row["herb_a"], "herb_b": row["herb_b"]}


@app.get("/api/incompat/history")
def list_pair_history(_user: dict = Depends(current_user)):
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, action, herb_a, herb_b, actor, created_at FROM incompat_history ORDER BY id DESC"
        ).fetchall()
    return rows


@app.get("/api/incompat/today")
def today_conflicts(_user: dict = Depends(current_user)):
    """当日冲突提示：每个禁忌对两味药今日（UTC+8 自然日）的写入情况。"""
    start, end = day_bounds()
    with connect() as conn:
        pairs = conn.execute("SELECT id, herb_a, herb_b FROM incompat_pairs ORDER BY id DESC").fetchall()
        rows = conn.execute(
            """SELECT herb, verdict FROM batches
               WHERE created_at >= %s AND created_at < %s
               ORDER BY id""",
            (start, end),
        ).fetchall()
    verdicts_by_herb: dict[str, list[str]] = {}
    for r in rows:
        verdicts_by_herb.setdefault(r["herb"], []).append(r["verdict"])
    return {
        "day": start.date().isoformat(),
        "pairs": [
            {
                "pair_id": p["id"],
                "herb_a": p["herb_a"],
                "herb_b": p["herb_b"],
                "a_verdicts": verdicts_by_herb.get(p["herb_a"], []),
                "b_verdicts": verdicts_by_herb.get(p["herb_b"], []),
            }
            for p in pairs
        ],
    }
