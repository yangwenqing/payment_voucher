"""数据库访问层：表结构初始化与全部增删改查函数。

约定：
- 所有函数通过 connectdb.get_conn() 获取连接，调用即提交。
- 凭单表保存的是录入时的快照，人员库/字典的后续修改不会影响历史凭单。
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Iterable

from connectdb import get_conn

SCHEMA = """
CREATE TABLE IF NOT EXISTS identities (
    id          BIGSERIAL PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    sort_order  INT NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS fee_types (
    id          BIGSERIAL PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    sort_order  INT NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS persons (
    id            BIGSERIAL PRIMARY KEY,
    name          TEXT NOT NULL,
    identity      TEXT NOT NULL,
    hire_date     DATE,
    contract_end  DATE,
    bank_name     TEXT NOT NULL DEFAULT '',
    bank_account  TEXT NOT NULL DEFAULT '',
    salary        NUMERIC(12, 2),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (name, bank_account)
);

CREATE SEQUENCE IF NOT EXISTS voucher_no_seq START 1;

CREATE TABLE IF NOT EXISTS vouchers (
    id            BIGSERIAL PRIMARY KEY,
    serial_no     INT NOT NULL,
    voucher_date  DATE NOT NULL DEFAULT CURRENT_DATE,
    name          TEXT NOT NULL,
    identity      TEXT NOT NULL,
    hire_date     DATE,
    contract_end  DATE,
    bank_name     TEXT NOT NULL DEFAULT '',
    bank_account  TEXT NOT NULL DEFAULT '',
    fee_type      TEXT,
    salary        NUMERIC(12, 2),
    amount        NUMERIC(12, 2) NOT NULL,
    period_month  TEXT,
    purpose       TEXT,
    person_id     BIGINT REFERENCES persons(id) ON DELETE SET NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS attendance (
    id              BIGSERIAL PRIMARY KEY,
    person_id       BIGINT REFERENCES persons(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    period_month    TEXT NOT NULL,
    salary          NUMERIC(12, 2) NOT NULL DEFAULT 0,
    expected_days   NUMERIC(5, 1) NOT NULL DEFAULT 0,
    online_days     NUMERIC(5, 1) NOT NULL DEFAULT 0,
    offline_days    NUMERIC(5, 1) NOT NULL DEFAULT 0,
    leave_days      NUMERIC(5, 1) NOT NULL DEFAULT 0,
    absent_days     NUMERIC(5, 1) NOT NULL DEFAULT 0,
    bonus           NUMERIC(12, 2) NOT NULL DEFAULT 0,
    note            TEXT NOT NULL DEFAULT '',
    online_amount   NUMERIC(12, 2) NOT NULL DEFAULT 0,
    offline_amount  NUMERIC(12, 2) NOT NULL DEFAULT 0,
    total_amount    NUMERIC(12, 2) NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (person_id, period_month)
);
CREATE INDEX IF NOT EXISTS idx_attendance_month ON attendance (period_month);

CREATE INDEX IF NOT EXISTS idx_vouchers_created ON vouchers (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_vouchers_name ON vouchers (name);

CREATE TABLE IF NOT EXISTS voucher_templates (
    id            BIGSERIAL PRIMARY KEY,
    title         TEXT NOT NULL,
    name          TEXT NOT NULL,
    identity      TEXT NOT NULL,
    hire_date     DATE,
    contract_end  DATE,
    bank_name     TEXT NOT NULL DEFAULT '',
    bank_account  TEXT NOT NULL DEFAULT '',
    fee_type      TEXT,
    salary        NUMERIC(12, 2),
    amount        NUMERIC(12, 2),
    period_month  TEXT,
    purpose       TEXT,
    person_id     BIGINT REFERENCES persons(id) ON DELETE SET NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

DEFAULT_IDENTITIES = ["职工", "社区专家", "社区实习生"]
DEFAULT_FEE_TYPES = [
    "差旅报销费用",
    "社区办公场地建设费用",
    "业务招待费用",
    "杂项费用",
    "工资",
    "技术服务费",
]

def normalize_month(value) -> str:
    """把月份统一为「YYYY年M月」。

    接受月份选择器的 "2026-10"、"2026年10月"、"10月"（补当前年份）等写法；
    无法识别时原样返回，空值返回空串。
    """
    text = str(value or "").strip()
    if not text:
        return ""
    m = re.fullmatch(r"(\d{4})[-/.年](\d{1,2})月?", text)
    if m:
        return f"{m.group(1)}年{int(m.group(2))}月"
    m = re.fullmatch(r"(\d{1,2})月", text)
    if m:
        return f"{date.today().year}年{int(m.group(1))}月"
    return text


def month_input_value(value) -> str:
    """「YYYY年M月」转月份选择器的 "YYYY-MM"；无法识别返回空串。"""
    text = str(value or "").strip()
    m = re.fullmatch(r"(\d{4})年(\d{1,2})月", text) or re.fullmatch(r"(\d{4})[-/.](\d{1,2})", text)
    if not m:
        return ""
    return f"{m.group(1)}-{int(m.group(2)):02d}"


_VOUCHER_FIELDS = [
    "name",
    "identity",
    "hire_date",
    "contract_end",
    "bank_name",
    "bank_account",
    "fee_type",
    "salary",
    "amount",
    "period_month",
    "purpose",
    "voucher_date",
    "person_id",
]

_TEMPLATE_FIELDS = [
    "title",
    "name",
    "identity",
    "hire_date",
    "contract_end",
    "bank_name",
    "bank_account",
    "fee_type",
    "salary",
    "amount",
    "period_month",
    "purpose",
    "person_id",
]


# ---------------------------------------------------------------- 初始化

def init_schema() -> None:
    """建表并写入默认字典（幂等）。"""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(SCHEMA)
        # 旧库迁移：persons 补充 salary 列
        cur.execute("ALTER TABLE persons ADD COLUMN IF NOT EXISTS salary NUMERIC(12, 2)")
        for i, name in enumerate(DEFAULT_IDENTITIES):
            cur.execute(
                "INSERT INTO identities (name, sort_order) VALUES (%s, %s) "
                "ON CONFLICT (name) DO NOTHING",
                (name, i),
            )
        for i, name in enumerate(DEFAULT_FEE_TYPES):
            cur.execute(
                "INSERT INTO fee_types (name, sort_order) VALUES (%s, %s) "
                "ON CONFLICT (name) DO NOTHING",
                (name, i),
            )


# ---------------------------------------------------------------- 字典

def _list_dict(table: str) -> list[dict]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT id, name FROM {table} ORDER BY sort_order, id")
        return cur.fetchall()


def _add_dict(table: str, name: str) -> dict:
    name = (name or "").strip()
    if not name:
        raise ValueError("名称不能为空")
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            f"INSERT INTO {table} (name, sort_order) VALUES (%s, "
            f"(SELECT COALESCE(MAX(sort_order), 0) + 1 FROM {table})) "
            "ON CONFLICT (name) DO NOTHING RETURNING id, name",
            (name,),
        )
        row = cur.fetchone()
        if row is None:
            raise ValueError(f"“{name}”已存在")
        return row


def _delete_dict(table: str, item_id: int) -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(f"DELETE FROM {table} WHERE id = %s", (item_id,))


def list_identities() -> list[dict]:
    return _list_dict("identities")


def add_identity(name: str) -> dict:
    return _add_dict("identities", name)


def delete_identity(item_id: int) -> None:
    _delete_dict("identities", item_id)


def list_fee_types() -> list[dict]:
    return _list_dict("fee_types")


def add_fee_type(name: str) -> dict:
    return _add_dict("fee_types", name)


def delete_fee_type(item_id: int) -> None:
    _delete_dict("fee_types", item_id)


# ---------------------------------------------------------------- 人员库

def list_persons(keyword: str | None = None) -> list[dict]:
    sql = "SELECT * FROM persons"
    params: list[Any] = []
    if keyword:
        sql += " WHERE name ILIKE %s OR bank_account ILIKE %s OR identity ILIKE %s"
        params = [f"%{keyword}%"] * 3
    sql += " ORDER BY id"
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def get_person(person_id: int) -> dict | None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM persons WHERE id = %s", (person_id,))
        return cur.fetchone()


def _clean_person(data: dict) -> dict:
    name = (data.get("name") or "").strip()
    identity = (data.get("identity") or "").strip()
    if not name:
        raise ValueError("姓名不能为空")
    if not identity:
        raise ValueError("身份不能为空")
    salary = data.get("salary")
    if salary in ("", None):
        salary = None
    else:
        try:
            salary = Decimal(str(salary))
        except Exception as exc:  # noqa: BLE001
            raise ValueError("薪资格式不正确") from exc
    return {
        "name": name,
        "identity": identity,
        "hire_date": data.get("hire_date") or None,
        "contract_end": data.get("contract_end") or None,
        "bank_name": (data.get("bank_name") or "").strip(),
        "bank_account": (data.get("bank_account") or "").replace(" ", "").strip(),
        "salary": salary,
    }


def create_person(data: dict) -> dict:
    cleaned = _clean_person(data)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO persons (name, identity, hire_date, contract_end, bank_name, bank_account, salary) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (name, bank_account) DO UPDATE SET "
            "identity = EXCLUDED.identity, hire_date = EXCLUDED.hire_date, "
            "contract_end = EXCLUDED.contract_end, bank_name = EXCLUDED.bank_name, "
            "salary = EXCLUDED.salary, updated_at = now() RETURNING *",
            (
                cleaned["name"],
                cleaned["identity"],
                cleaned["hire_date"],
                cleaned["contract_end"],
                cleaned["bank_name"],
                cleaned["bank_account"],
                cleaned["salary"],
            ),
        )
        return cur.fetchone()


def update_person(person_id: int, data: dict) -> dict | None:
    cleaned = _clean_person(data)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE persons SET name = %s, identity = %s, hire_date = %s, contract_end = %s, "
            "bank_name = %s, bank_account = %s, salary = %s, updated_at = now() "
            "WHERE id = %s RETURNING *",
            (
                cleaned["name"],
                cleaned["identity"],
                cleaned["hire_date"],
                cleaned["contract_end"],
                cleaned["bank_name"],
                cleaned["bank_account"],
                cleaned["salary"],
                person_id,
            ),
        )
        return cur.fetchone()


def delete_person(person_id: int) -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM persons WHERE id = %s", (person_id,))


def import_persons(rows: Iterable[dict]) -> int:
    """导入人员，姓名+账号重复时更新。返回处理条数。"""
    count = 0
    with get_conn() as conn, conn.cursor() as cur:
        for row in rows:
            name = (row.get("name") or "").strip()
            if not name:
                continue
            cur.execute(
                "INSERT INTO persons (name, identity, hire_date, contract_end, bank_name, bank_account, salary) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (name, bank_account) DO UPDATE SET "
                "identity = EXCLUDED.identity, hire_date = EXCLUDED.hire_date, "
                "contract_end = EXCLUDED.contract_end, bank_name = EXCLUDED.bank_name, "
                "salary = EXCLUDED.salary, updated_at = now()",
                (
                    name,
                    (row.get("identity") or "职工").strip(),
                    row.get("hire_date"),
                    row.get("contract_end"),
                    (row.get("bank_name") or "").strip(),
                    (row.get("bank_account") or "").replace(" ", "").strip(),
                    row.get("salary"),
                ),
            )
            count += 1
    return count


# ---------------------------------------------------------------- 凭单

def next_serial_no() -> int:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT nextval('voucher_no_seq') AS no")
        return int(cur.fetchone()["no"])


def peek_next_serial_no() -> int:
    """预览用：读取下一个编号但不占用序列。"""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT CASE WHEN is_called THEN last_value + 1 ELSE last_value END AS no "
            "FROM voucher_no_seq"
        )
        return int(cur.fetchone()["no"])


def _clean_voucher(data: dict) -> dict:
    cleaned = {}
    for field in _VOUCHER_FIELDS:
        value = data.get(field)
        if isinstance(value, str):
            value = value.strip()
            if field in {"hire_date", "contract_end", "voucher_date"}:
                value = value or None
        cleaned[field] = value
    if not cleaned["name"]:
        raise ValueError("姓名不能为空")
    if not cleaned["identity"]:
        raise ValueError("身份不能为空")
    if not cleaned["fee_type"]:
        raise ValueError("费用类型不能为空")
    if cleaned["amount"] in (None, ""):
        raise ValueError("金额不能为空")
    cleaned["amount"] = Decimal(str(cleaned["amount"]))
    if cleaned["amount"] <= 0:
        raise ValueError("金额必须大于 0")
    if cleaned["salary"] in ("", None):
        cleaned["salary"] = None
    else:
        cleaned["salary"] = Decimal(str(cleaned["salary"]))
    cleaned["bank_account"] = (cleaned["bank_account"] or "").replace(" ", "")
    cleaned["period_month"] = normalize_month(cleaned["period_month"])
    if not cleaned["voucher_date"]:
        cleaned["voucher_date"] = date.today().isoformat()
    return cleaned


def create_voucher(data: dict) -> dict:
    cleaned = _clean_voucher(data)
    cleaned["serial_no"] = next_serial_no()
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO vouchers (serial_no, voucher_date, name, identity, hire_date, contract_end, "
            "bank_name, bank_account, fee_type, salary, amount, period_month, purpose, person_id) "
            "VALUES (%(serial_no)s, %(voucher_date)s, %(name)s, %(identity)s, %(hire_date)s, "
            "%(contract_end)s, %(bank_name)s, %(bank_account)s, %(fee_type)s, %(salary)s, "
            "%(amount)s, %(period_month)s, %(purpose)s, %(person_id)s) RETURNING *",
            cleaned,
        )
        return cur.fetchone()


def update_voucher(voucher_id: int, data: dict) -> dict | None:
    cleaned = _clean_voucher(data)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE vouchers SET voucher_date = %(voucher_date)s, name = %(name)s, "
            "identity = %(identity)s, hire_date = %(hire_date)s, contract_end = %(contract_end)s, "
            "bank_name = %(bank_name)s, bank_account = %(bank_account)s, fee_type = %(fee_type)s, "
            "salary = %(salary)s, amount = %(amount)s, period_month = %(period_month)s, "
            "purpose = %(purpose)s, person_id = %(person_id)s, updated_at = now() "
            "WHERE id = %(id)s RETURNING *",
            {**cleaned, "id": voucher_id},
        )
        return cur.fetchone()


def delete_voucher(voucher_id: int) -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM vouchers WHERE id = %s", (voucher_id,))


def get_voucher(voucher_id: int) -> dict | None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM vouchers WHERE id = %s", (voucher_id,))
        return cur.fetchone()


def list_vouchers(keyword: str | None = None, limit: int = 200) -> list[dict]:
    sql = "SELECT * FROM vouchers"
    params: list[Any] = []
    if keyword:
        sql += (
            " WHERE name ILIKE %s OR identity ILIKE %s OR fee_type ILIKE %s "
            "OR bank_account ILIKE %s OR CAST(serial_no AS TEXT) = %s"
        )
        params = [f"%{keyword}%", f"%{keyword}%", f"%{keyword}%", f"%{keyword}%", keyword]
    sql += " ORDER BY created_at DESC, id DESC LIMIT %s"
    params.append(limit)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


# ---------------------------------------------------------------- 模板

def _clean_template(data: dict) -> dict:
    cleaned = {f: data.get(f) for f in _TEMPLATE_FIELDS}
    for key in ("title", "name", "identity", "bank_name", "bank_account",
                "fee_type", "period_month", "purpose"):
        if isinstance(cleaned.get(key), str):
            cleaned[key] = cleaned[key].strip()
    for key in ("hire_date", "contract_end"):
        if isinstance(cleaned.get(key), str):
            cleaned[key] = cleaned[key].strip() or None
    if not cleaned["name"]:
        raise ValueError("模板中的姓名不能为空")
    if not cleaned["identity"]:
        raise ValueError("模板中的身份不能为空")
    if not cleaned["title"]:
        cleaned["title"] = f"{cleaned['name']} · {cleaned['identity']} 模板"
    for key in ("salary", "amount"):
        value = cleaned.get(key)
        cleaned[key] = None if value in ("", None) else Decimal(str(value))
    return cleaned


def create_template(data: dict) -> dict:
    cleaned = _clean_template(data)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO voucher_templates (title, name, identity, hire_date, contract_end, bank_name, "
            "bank_account, fee_type, salary, amount, period_month, purpose, person_id) "
            "VALUES (%(title)s, %(name)s, %(identity)s, %(hire_date)s, %(contract_end)s, "
            "%(bank_name)s, %(bank_account)s, %(fee_type)s, %(salary)s, %(amount)s, "
            "%(period_month)s, %(purpose)s, %(person_id)s) RETURNING *",
            cleaned,
        )
        return cur.fetchone()


def update_template(template_id: int, data: dict) -> dict | None:
    cleaned = _clean_template(data)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE voucher_templates SET title = %(title)s, name = %(name)s, identity = %(identity)s, "
            "hire_date = %(hire_date)s, contract_end = %(contract_end)s, bank_name = %(bank_name)s, "
            "bank_account = %(bank_account)s, fee_type = %(fee_type)s, salary = %(salary)s, "
            "amount = %(amount)s, period_month = %(period_month)s, purpose = %(purpose)s, "
            "person_id = %(person_id)s, updated_at = now() WHERE id = %(id)s RETURNING *",
            {**cleaned, "id": template_id},
        )
        return cur.fetchone()


def delete_template(template_id: int) -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM voucher_templates WHERE id = %s", (template_id,))


def get_template(template_id: int) -> dict | None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM voucher_templates WHERE id = %s", (template_id,))
        return cur.fetchone()


def list_templates() -> list[dict]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM voucher_templates ORDER BY updated_at DESC, id DESC")
        return cur.fetchall()


# ---------------------------------------------------------------- 考勤与工资测算

ONLINE_MONTHLY_BASE = Decimal("1500")  # 线上出勤工资基数（月）

_ATTENDANCE_DAYS = ["expected_days", "online_days", "offline_days",
                    "leave_days", "absent_days"]


def compute_attendance(row: dict) -> dict:
    """工资测算：线上合计 = 1500×线上/应出勤；线下合计 = 应发薪资×线下/应出勤；总计 = 两者 + 奖金。"""
    out = dict(row)
    expected = Decimal(str(out.get("expected_days") or 0))
    online = Decimal(str(out.get("online_days") or 0))
    offline = Decimal(str(out.get("offline_days") or 0))
    salary = Decimal(str(out.get("salary") or 0))
    bonus = Decimal(str(out.get("bonus") or 0))

    if expected > 0:
        online_amount = (ONLINE_MONTHLY_BASE * online / expected).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP)
        offline_amount = (salary * offline / expected).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP)
    else:
        online_amount = offline_amount = Decimal("0")
    out["online_amount"] = online_amount
    out["offline_amount"] = offline_amount
    out["total_amount"] = (online_amount + offline_amount + bonus).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP)
    return out


def _clean_attendance(data: dict) -> dict:
    cleaned = {
        "person_id": data.get("person_id"),
        "name": (data.get("name") or "").strip(),
        "period_month": (data.get("period_month") or "").strip(),
        "salary": data.get("salary") or 0,
        "bonus": data.get("bonus") or 0,
        "note": (data.get("note") or "").strip(),
    }
    for field in _ATTENDANCE_DAYS:
        value = data.get(field)
        cleaned[field] = Decimal(str(value)) if value not in ("", None) else Decimal("0")
    if not cleaned["person_id"]:
        raise ValueError("考勤记录必须关联人员库中的人员")
    cleaned["period_month"] = normalize_month(cleaned["period_month"])
    if not cleaned["period_month"]:
        raise ValueError("考勤月份不能为空")
    person = get_person(int(cleaned["person_id"]))
    if person is None:
        raise ValueError("人员不存在")
    cleaned["name"] = person["name"]
    for field in ("salary", "bonus"):
        cleaned[field] = Decimal(str(cleaned[field]))
    return cleaned


def upsert_attendance(data: dict) -> dict:
    """保存考勤记录（按人员+月份），实时计算并返回测算结果。"""
    cleaned = _clean_attendance(data)
    computed = compute_attendance(cleaned)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO attendance (person_id, name, period_month, salary, expected_days, "
            "online_days, offline_days, leave_days, absent_days, bonus, note, "
            "online_amount, offline_amount, total_amount) "
            "VALUES (%(person_id)s, %(name)s, %(period_month)s, %(salary)s, %(expected_days)s, "
            "%(online_days)s, %(offline_days)s, %(leave_days)s, %(absent_days)s, %(bonus)s, "
            "%(note)s, %(online_amount)s, %(offline_amount)s, %(total_amount)s) "
            "ON CONFLICT (person_id, period_month) DO UPDATE SET "
            "salary = EXCLUDED.salary, expected_days = EXCLUDED.expected_days, "
            "online_days = EXCLUDED.online_days, offline_days = EXCLUDED.offline_days, "
            "leave_days = EXCLUDED.leave_days, absent_days = EXCLUDED.absent_days, "
            "bonus = EXCLUDED.bonus, note = EXCLUDED.note, "
            "online_amount = EXCLUDED.online_amount, offline_amount = EXCLUDED.offline_amount, "
            "total_amount = EXCLUDED.total_amount, updated_at = now() RETURNING *",
            computed,
        )
        row = cur.fetchone()
        # 同步回人员库的薪资属性，考勤页与人员库保持一致
        cur.execute(
            "UPDATE persons SET salary = %s, updated_at = now() WHERE id = %s",
            (computed["salary"], computed["person_id"]),
        )
        return row


def list_attendance(period_month: str) -> list[dict]:
    """人员库 LEFT JOIN 指定月份的考勤；应发薪资取考勤值，缺省回退到人员库薪资属性。"""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT p.id AS person_id, p.name, p.identity, "
            "a.id AS attendance_id, COALESCE(a.salary, p.salary) AS salary, "
            "a.expected_days, a.online_days, "
            "a.offline_days, a.leave_days, a.absent_days, a.bonus, a.note, "
            "a.online_amount, a.offline_amount, a.total_amount "
            "FROM persons p "
            "LEFT JOIN attendance a ON a.person_id = p.id AND a.period_month = %s "
            "ORDER BY p.id",
            (period_month,),
        )
        return cur.fetchall()


def get_attendance_amount(person_id: int, period_month: str) -> dict | None:
    """取某人员某月的测算金额，供制单页默认填充。"""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT salary, total_amount FROM attendance "
            "WHERE person_id = %s AND period_month = %s",
            (person_id, period_month),
        )
        return cur.fetchone()
