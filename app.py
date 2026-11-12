"""支付凭单生成系统 Web 应用入口。

启动：python3 app.py  → http://127.0.0.1:5000
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import psycopg
from flask import (
    Flask,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)

import db
import security
import voucher
from connectdb import check_connection, db_settings, is_configured

app = Flask(__name__)
app.json.ensure_ascii = False
app.secret_key = security.load_key()

BASE_DIR = Path(__file__).resolve().parent


# ---------------------------------------------------------------- 登录控制

@app.before_request
def require_login():
    """除登录页与静态资源外，所有页面与接口均需登录。"""
    if request.endpoint in (None, "login", "static"):
        return None
    if session.get("user"):
        return None
    if request.path.startswith("/api/"):
        return jsonify({"error": "未登录或登录已过期"}), 401
    return redirect(url_for("login", next=request.full_path.rstrip("?")))


@app.route("/login", methods=["GET", "POST"])
def login():
    admin = security.get_admin()
    error = ""
    next_url = request.values.get("next") or "/"
    if not next_url.startswith("/"):
        next_url = "/"

    if request.method == "POST":
        username = (request.form.get("username") or "").strip() or "admin"
        password = request.form.get("password") or ""
        if not admin:
            # 首次使用：初始化管理员账号
            if len(password) < 6:
                error = "首次设置：密码至少 6 位"
            else:
                security.set_admin(username, password)
                session["user"] = username
                return redirect(next_url)
        elif security.verify_admin(username, password):
            session["user"] = username
            return redirect(next_url)
        else:
            error = "用户名或密码不正确"
    return render_template("login.html", setup=admin is None, error=error, next=next_url)


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------------------------------------------------------------- 工具

def _json_date(value):
    if isinstance(value, (date, datetime)):
        return value.strftime("%Y-%m-%d")
    return value


def _serialize(row: dict | None) -> dict | None:
    if row is None:
        return None
    out = {}
    for key, value in row.items():
        if isinstance(value, Decimal):
            out[key] = float(value)
        else:
            out[key] = _json_date(value)
    return out


def _payload() -> dict:
    data = request.get_json(silent=True) or request.form.to_dict()
    return {key: (value if value != "" else None) for key, value in data.items()}


@app.errorhandler(ValueError)
def _handle_value_error(exc: ValueError):
    return jsonify({"error": str(exc)}), 400


@app.errorhandler(RuntimeError)
def _handle_runtime_error(exc: RuntimeError):
    """数据库未配置等运行时问题。"""
    return jsonify({"error": str(exc)}), 503


@app.errorhandler(psycopg.Error)
def _handle_db_error(exc: psycopg.Error):
    return jsonify({"error": f"数据库操作失败：{exc}"}), 503


@app.template_filter("money")
def _money(value):
    if value is None:
        return "—"
    return voucher.format_amount(value)


@app.template_filter("d")
def _date_filter(value):
    if not value:
        return "—"
    return _json_date(value)


@app.context_processor
def inject_globals():
    ok, _ = check_connection()
    settings = db_settings()
    # 静态资源缓存版本：文件更新后浏览器自动拉取最新 js/css
    static_v = 0
    for name in ("app.js", "app.css"):
        try:
            static_v = max(static_v, int((Path(__file__).parent / "static" / name).stat().st_mtime))
        except OSError:
            pass
    return {
        "db_ok": ok,
        "db_name": settings["dbname"] or "未配置",
        "db_ready": is_configured(),
        "current_user": session.get("user") or "",
        "static_v": static_v,
    }


@app.get("/api/db-config")
def api_get_db_config():
    s = db_settings()
    return jsonify(
        {
            "host": s["host"],
            "port": s["port"],
            "dbname": s["dbname"],
            "user": s["user"],
            "has_password": bool(s["password"]),
            "configured": is_configured(),
        }
    )


@app.post("/api/db-config")
def api_set_db_config():
    """保存数据库连接信息（加密落盘）并立即测试连接。"""
    data = _payload()
    current = security.get_db_config()
    db_conf = {
        "host": (data.get("host") or "").strip(),
        "port": str(data.get("port") or "").strip() or "5432",
        "dbname": (data.get("dbname") or "").strip(),
        "user": (data.get("user") or "").strip(),
        # 密码留空表示沿用已保存的密码
        "password": data.get("password") or current.get("password") or "",
    }
    if not db_conf["host"] or not db_conf["dbname"] or not db_conf["user"]:
        raise ValueError("主机、数据库名与用户名不能为空")
    security.set_db_config(db_conf)
    ok, message = check_connection()
    if ok:
        db.init_schema()
    return jsonify({"ok": True, "connected": ok, "error": message})


# ---------------------------------------------------------------- 页面

@app.get("/")
def index():
    return render_template(
        "index.html",
        identities=db.list_identities() if _db_ready() else [],
        fee_types=db.list_fee_types() if _db_ready() else [],
        persons=db.list_persons() if _db_ready() else [],
        today=date.today().strftime("%Y-%m-%d"),
        month_input=date.today().strftime("%Y-%m"),
        page="index",
    )


@app.get("/history")
def history():
    keyword = request.args.get("q", "").strip()
    rows = db.list_vouchers(keyword or None) if _db_ready() else []
    return render_template("history.html", rows=rows, keyword=keyword, page="history")


@app.get("/templates")
def templates():
    rows = db.list_templates() if _db_ready() else []
    return render_template(
        "templates.html",
        rows=rows,
        identities=db.list_identities() if _db_ready() else [],
        page="templates",
    )


@app.get("/settings")
def settings():
    return render_template(
        "settings.html",
        identities=db.list_identities() if _db_ready() else [],
        fee_types=db.list_fee_types() if _db_ready() else [],
        persons=db.list_persons() if _db_ready() else [],
        page="settings",
    )


@app.get("/attendance")
def attendance():
    month = db.normalize_month(request.args.get("month", "")) or default_month()
    rows = db.list_attendance(month) if _db_ready() else []
    return render_template(
        "attendance.html",
        rows=[_serialize(r) for r in rows],
        month=month,
        month_input=db.month_input_value(month) or date.today().strftime("%Y-%m"),
        page="attendance",
    )


@app.get("/batch")
def batch():
    month = default_month()
    return render_template(
        "batch.html",
        persons=db.list_persons() if _db_ready() else [],
        fee_types=db.list_fee_types() if _db_ready() else [],
        identities=db.list_identities() if _db_ready() else [],
        today=date.today().strftime("%Y-%m-%d"),
        month=month,
        month_input=db.month_input_value(month),
        page="batch",
    )


def default_month() -> str:
    return f"{date.today().year}年{date.today().month}月"


def _db_ready() -> bool:
    ok, _ = check_connection()
    return ok


# ---------------------------------------------------------------- 预览

@app.post("/api/preview")
def api_preview():
    data = _payload()
    if data.get("id"):
        record = db.get_voucher(int(data["id"]))
        if record is None:
            raise ValueError("凭单不存在")
    else:
        if not data.get("serial_no"):
            try:
                data["serial_no"] = db.peek_next_serial_no()
            except Exception:  # noqa: BLE001 数据库未就绪时仅预览文字
                data["serial_no"] = ""
        record = data

    amount = data.get("amount")
    if amount in (None, ""):
        raise ValueError("请填写支付金额")
    try:
        Decimal(str(amount))
    except Exception as exc:  # noqa: BLE001
        raise ValueError("金额格式不正确") from exc

    context = voucher.build_context(record)
    html = render_template("_paper.html", ctx=context)
    return jsonify({"html": html, "context": context})


# ---------------------------------------------------------------- 凭单

@app.get("/api/vouchers")
def api_list_vouchers():
    return jsonify([_serialize(row) for row in db.list_vouchers(request.args.get("q"))])


@app.post("/api/vouchers")
def api_create_voucher():
    record = db.create_voucher(_payload())
    return jsonify(_serialize(record)), 201


@app.get("/api/vouchers/<int:voucher_id>")
def api_get_voucher(voucher_id: int):
    record = db.get_voucher(voucher_id)
    if record is None:
        raise ValueError("凭单不存在")
    return jsonify(_serialize(record))


@app.put("/api/vouchers/<int:voucher_id>")
def api_update_voucher(voucher_id: int):
    record = db.update_voucher(voucher_id, _payload())
    if record is None:
        raise ValueError("凭单不存在")
    return jsonify(_serialize(record))


@app.delete("/api/vouchers/<int:voucher_id>")
def api_delete_voucher(voucher_id: int):
    db.delete_voucher(voucher_id)
    return jsonify({"ok": True})


@app.get("/api/vouchers/<int:voucher_id>/docx")
def api_voucher_docx(voucher_id: int):
    record = db.get_voucher(voucher_id)
    if record is None:
        raise ValueError("凭单不存在")
    path = voucher.build_docx(record)
    return send_file(path, as_attachment=True, download_name=path.name)


@app.post("/api/vouchers/batch")
def api_batch_vouchers():
    """批量生成：多张凭单合并为一个 docx，每张凭单独占一页。"""
    data = request.get_json(silent=True) or {}
    items = data.get("items") or []
    if not isinstance(items, list) or not items:
        raise ValueError("请至少勾选一位人员")
    records = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("批量数据格式不正确")
        records.append(db.create_voucher(item))
    path = voucher.build_batch_docx(records)
    return jsonify(
        {
            "created": [_serialize(r) for r in records],
            "download": f"/api/batch-docx/{path.name}",
        }
    )


@app.get("/api/batch-docx/<path:filename>")
def api_batch_docx(filename: str):
    """下载最近批量生成的合并文件（仅允许 output 目录内的 docx）。"""
    target = (voucher.OUTPUT_DIR / filename).resolve()
    if target.suffix.lower() != ".docx" or not target.is_file() or \
            voucher.OUTPUT_DIR.resolve() not in target.parents:
        raise ValueError("文件不存在")
    return send_file(target, as_attachment=True, download_name=target.name)


# ---------------------------------------------------------------- 模板

@app.get("/api/templates")
def api_list_templates():
    return jsonify([_serialize(row) for row in db.list_templates()])


@app.post("/api/templates")
def api_create_template():
    return jsonify(_serialize(db.create_template(_payload()))), 201


@app.get("/api/templates/<int:template_id>")
def api_get_template(template_id: int):
    record = db.get_template(template_id)
    if record is None:
        raise ValueError("模板不存在")
    return jsonify(_serialize(record))


@app.put("/api/templates/<int:template_id>")
def api_update_template(template_id: int):
    record = db.update_template(template_id, _payload())
    if record is None:
        raise ValueError("模板不存在")
    return jsonify(_serialize(record))


@app.delete("/api/templates/<int:template_id>")
def api_delete_template(template_id: int):
    db.delete_template(template_id)
    return jsonify({"ok": True})


# ---------------------------------------------------------------- 考勤与工资测算

@app.get("/api/attendance")
def api_list_attendance():
    month = db.normalize_month(request.args.get("month", "")) or default_month()
    return jsonify({"month": month, "rows": [_serialize(r) for r in db.list_attendance(month)]})


@app.post("/api/attendance")
def api_save_attendance():
    """保存单条考勤并实时返回测算结果。"""
    row = db.upsert_attendance(_payload())
    return jsonify(_serialize(row))


@app.get("/api/attendance/amount")
def api_attendance_amount():
    """按人员与月份取测算金额，供制单/批量页默认填充。"""
    person_id = request.args.get("person_id", type=int)
    month = db.normalize_month(request.args.get("month", "")) or default_month()
    if not person_id:
        raise ValueError("缺少人员")
    row = db.get_attendance_amount(person_id, month)
    if row is None:
        return jsonify({"found": False})
    return jsonify(
        {"found": True, "amount": float(row["total_amount"]), "salary": float(row["salary"] or 0)}
    )


@app.get("/api/attendance/export")
def api_export_attendance():
    """导出工资测算依据表（xlsx），版式与《工资计算依据.xlsx》一致。"""
    import io

    import openpyxl

    month = db.normalize_month(request.args.get("month", "")) or default_month()
    rows = db.list_attendance(month)

    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "工资测算依据"
    headers = [
        "姓名", f"{month}应出勤", "请假", "缺勤", "线上", "线上合计",
        "线下", "线下合计", "总计", "备注",
    ]
    sheet.append(headers)
    for row in rows:
        if row.get("attendance_id") is None and row.get("expected_days") is None:
            continue
        note_parts = []
        bonus = row.get("bonus") or 0
        if bonus:
            note_parts.append(f"奖金 {voucher.format_amount(bonus)}")
        if row.get("note"):
            note_parts.append(str(row["note"]))
        sheet.append(
            [
                row["name"],
                _num(row.get("expected_days")),
                _num(row.get("leave_days")),
                _num(row.get("absent_days")),
                _num(row.get("online_days")),
                _num(row.get("online_amount")),
                _num(row.get("offline_days")),
                _num(row.get("offline_amount")),
                _num(row.get("total_amount")),
                "；".join(note_parts),
            ]
        )
    for col, width in (("B", 10.125), ("C", 9.0), ("D", 8.0), ("E", 8.0),
                       ("F", 9.375), ("G", 8.0), ("H", 9.375), ("I", 9.0), ("J", 30.375)):
        sheet.column_dimensions[col].width = width
    for cell in sheet[1]:
        cell.font = openpyxl.styles.Font(bold=True)

    buffer = io.BytesIO()
    book.save(buffer)
    buffer.seek(0)
    return send_file(
        buffer,
        as_attachment=True,
        download_name=f"工资测算依据表_{month}_{date.today():%Y%m%d}.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


def _num(value):
    if value in (None, ""):
        return None
    value = float(value)
    return int(value) if value == int(value) else value


# ---------------------------------------------------------------- 字典与人员

@app.get("/api/options")
def api_options():
    return jsonify(
        {
            "identities": db.list_identities(),
            "fee_types": db.list_fee_types(),
            "persons": [_serialize(row) for row in db.list_persons()],
            "next_serial_no": db.peek_next_serial_no(),
        }
    )


@app.post("/api/identities")
def api_add_identity():
    return jsonify(db.add_identity((_payload().get("name") or ""))), 201


@app.delete("/api/identities/<int:item_id>")
def api_delete_identity(item_id: int):
    db.delete_identity(item_id)
    return jsonify({"ok": True})


@app.post("/api/fee-types")
def api_add_fee_type():
    return jsonify(db.add_fee_type((_payload().get("name") or ""))), 201


@app.delete("/api/fee-types/<int:item_id>")
def api_delete_fee_type(item_id: int):
    db.delete_fee_type(item_id)
    return jsonify({"ok": True})


@app.get("/api/persons")
def api_list_persons():
    return jsonify([_serialize(row) for row in db.list_persons(request.args.get("q"))])


@app.post("/api/persons")
def api_create_person():
    return jsonify(_serialize(db.create_person(_payload()))), 201


@app.put("/api/persons/<int:person_id>")
def api_update_person(person_id: int):
    record = db.update_person(person_id, _payload())
    if record is None:
        raise ValueError("人员不存在")
    return jsonify(_serialize(record))


@app.delete("/api/persons/<int:person_id>")
def api_delete_person(person_id: int):
    db.delete_person(person_id)
    return jsonify({"ok": True})


def _read_table_rows(file_storage) -> list[tuple]:
    """把上传的任意表格文件（xlsx/xlsm/xls/csv/tsv）读成行列表。"""
    filename = file_storage.filename or ""
    ext = Path(filename).suffix.lower()
    raw = file_storage.read()

    if ext in (".xlsx", ".xlsm"):
        import io

        import openpyxl

        book = openpyxl.load_workbook(io.BytesIO(raw), data_only=True, read_only=True)
        fallback: list[tuple] | None = None
        for sheet in book.worksheets:
            rows = [tuple(row) for row in sheet.iter_rows(values_only=True)]
            if fallback is None:
                fallback = rows
            if any("姓名" in [str(cell or "").strip() for cell in row] for row in rows[:10]):
                return rows
        return fallback or []

    if ext == ".xls":
        import xlrd

        book = xlrd.open_workbook(file_contents=raw)

        def xlrd_value(cell):
            if cell.ctype == xlrd.XL_CELL_DATE:
                return xlrd.xldate.xldate_as_datetime(cell.value, book.datemode)
            return cell.value

        def sheet_rows(sheet):
            return [
                tuple(xlrd_value(sheet.cell(r, c)) for c in range(sheet.ncols))
                for r in range(sheet.nrows)
            ]

        fallback = None
        for idx in range(book.nsheets):
            rows = sheet_rows(book.sheet_by_index(idx))
            if fallback is None:
                fallback = rows
            if any("姓名" in [str(cell or "").strip() for cell in row] for row in rows[:10]):
                return rows
        return fallback or []

    if ext in (".csv", ".tsv", ".txt"):
        import csv
        import io

        text = None
        for encoding in ("utf-8-sig", "gbk", "utf-16", "latin-1"):
            try:
                text = raw.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            raise ValueError("无法识别文件编码")
        delimiter = "\t" if ext == ".tsv" else ","
        return [tuple(row) for row in csv.reader(io.StringIO(text), delimiter=delimiter)]

    raise ValueError("仅支持 xlsx / xlsm / xls / csv / tsv 表格文件")


def _persons_payload(rows: list[tuple]) -> list[dict]:
    """按表头把行列表转成人员导入数据；表头行在前 10 行内自动识别。"""
    header_index = -1
    index: dict[str, int] = {}
    for pos, row in enumerate(rows[:10]):
        names = [str(cell or "").strip() for cell in row]
        if "姓名" in names:
            header_index = pos
            index = {name: i for i, name in enumerate(names)}
            break
    if header_index < 0:
        raise ValueError("未找到表头行：首行（或前 10 行内）需包含“姓名”列")

    def cell(row, *names):
        for name in names:
            pos = index.get(name)
            if pos is not None and pos < len(row):
                return row[pos]
        return None

    payload = []
    for row in rows[header_index + 1:]:
        if not any(row):
            continue
        hire = cell(row, "起始时间", "入职时间")
        end = cell(row, "截止时间", "合同到期时间")
        payload.append(
            {
                "name": str(cell(row, "姓名") or "").strip(),
                "identity": str(cell(row, "身份") or "职工").strip(),
                "hire_date": _json_date(hire) if hire else None,
                "contract_end": _json_date(end) if end else None,
                "bank_name": str(cell(row, "开户行") or "").strip(),
                "bank_account": str(cell(row, "银行卡号", "银行账号") or "").strip(),
                "salary": _parse_salary(cell(row, "薪资", "应发薪资", "月薪资", "工资")),
            }
        )
    return payload


def _parse_salary(value) -> float | None:
    """表格里的薪资列转数值；无法识别或为空返回 None。"""
    if value in (None, ""):
        return None
    text = str(value).replace(",", "").replace("，", "").replace("¥", "").replace("￥", "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


@app.post("/api/persons/import-file")
def api_import_persons():
    """上传任意表格文件（xlsx/xlsm/xls/csv/tsv）批量导入人员档案。"""
    file = request.files.get("file")
    if file is None or not file.filename:
        raise ValueError("请选择要导入的表格文件")
    rows = _read_table_rows(file)
    if not rows:
        raise ValueError("表格为空")
    return jsonify({"imported": db.import_persons(_persons_payload(rows))})


if __name__ == "__main__":
    try:
        db.init_schema()
        print("数据库初始化完成：", db_settings()["dbname"])
    except Exception as exc:  # noqa: BLE001
        print("数据库不可用，界面将提示连接配置：", exc)
    app.run(host="127.0.0.1", port=5000, debug=True)
