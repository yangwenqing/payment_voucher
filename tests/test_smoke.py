"""CI 冒烟测试：不依赖数据库，验证核心纯函数与页面可渲染。
"""

from __future__ import annotations

import pytest

import db
import voucher


def test_amount_to_chinese():
    assert voucher.amount_to_chinese("10512.5") == "壹万零伍佰壹拾贰元伍角"


def test_compute_attendance_formula():
    row = db.compute_attendance(
        {
            "salary": 8000,
            "expected_days": 20,
            "online_days": 3,
            "offline_days": 17,
            "bonus": 100,
        }
    )
    # 1500×3/20 = 225；8000×17/20 = 6800；总计 = 225+6800+100 = 7125
    assert row["online_amount"] == 225.00
    assert row["offline_amount"] == 6800.00
    assert row["total_amount"] == 7125.00


def test_compute_attendance_zero_expected():
    row = db.compute_attendance({"salary": 8000, "expected_days": 0, "online_days": 5, "bonus": 50})
    assert row["online_amount"] == 0
    assert row["offline_amount"] == 0
    assert row["total_amount"] == 50.00


def test_normalize_month():
    assert db.normalize_month("2026-10") == "2026年10月"
    assert db.normalize_month("2026/10") == "2026年10月"
    assert db.normalize_month("2026年10月") == "2026年10月"
    assert db.normalize_month("") == ""
    assert db.month_input_value("2026年10月") == "2026-10"


def test_clean_voucher_rejects_amount():
    with pytest.raises(ValueError):
        db._clean_voucher(
            {
                "name": "张三",
                "identity": "职工",
                "fee_type": "工资",
                "amount": 0,
            }
        )


def test_login_page_renders():
    from app import app as flask_app

    client = flask_app.test_client()
    resp = client.get("/login")
    assert resp.status_code == 200


def test_preview_requires_amount():
    from app import app as flask_app

    client = flask_app.test_client()
    # 未登录 → 401
    assert client.post("/api/preview", json={}).status_code == 401
    resp = client.post("/login", data={"username": "admin", "password": "secret123"})
    if resp.status_code != 302:
        # 本地已有管理员配置且密码不同，跳过登录后断言（CI 全新环境会初始化管理员）
        pytest.skip("已有管理员配置，跳过登录后断言")
    # 已登录但缺金额 → 400
    assert client.post("/api/preview", json={}).status_code == 400
