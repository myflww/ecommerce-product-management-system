"""Activity filtering uses stable identities and explicit Chinese calendar days."""
import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend


@pytest.fixture
def client(tmp_path):
    backend.init_storage(tmp_path)
    backend._login_attempts.clear()
    app = FastAPI()
    app.include_router(backend.router)
    with TestClient(app) as client:
        assert client.post("/api/setup", json={"name": "筛选管理员", "password": "activity-test-password"}).status_code == 200
        with backend._connection(write=True) as conn:
            conn.execute("DELETE FROM activity")
            admin = conn.execute("SELECT * FROM users LIMIT 1").fetchone()
            for identity, name in (("operator-a", "新运营甲"), ("operator-b", "新运营乙")):
                conn.execute("INSERT INTO users VALUES(?,?,?,?,?)", (identity, name, admin["password_hash"], "operator", backend.now()))
        yield client


def record(user_id="operator-a", actor="旧运营名", at="2026-11-13T01:00:00.000000+00:00", description="更新商品", *, action="update", batch_id=None):
    with backend._connection(write=True) as conn:
        cursor = conn.execute("INSERT INTO activity(user_id,actor,action,description,batch_id,created_at) VALUES(?,?,?,?,?,?)",
                              (user_id, actor, action, description, batch_id, at))
        return cursor.lastrowid


def items(client, **params):
    response = client.get("/api/activity", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_no_filters_returns_all_and_original_fields(client):
    record(description="较早", at="2026-11-12T09:00:00.000000+00:00")
    latest = record(user_id="operator-b", actor="乙", description="较新")
    result = items(client)
    assert result["total"] == 2
    assert result["items"][0]["id"] == latest
    assert result["items"][0]["actor"] == "乙"
    assert result["items"][0]["description"] == "较新"
    assert result["items"][0]["can_undo"] is False
    assert {a["id"] for a in result["actors"]} == {"operator-a", "operator-b"}


def test_china_calendar_date_includes_midnight_and_full_end_day(client):
    record(at="2026-11-12T15:59:59.999999+00:00", description="中国13日之前")
    record(at="2026-11-12T16:00:00.000000+00:00", description="中国13日零点")
    # Legacy whole-second ISO timestamps remain included at the exact boundary.
    record(at="2026-11-12T16:00:00+00:00", description="中国13日零点无小数")
    record(at="2026-11-13T15:59:59.999999+00:00", description="中国13日最后一刻")
    record(at="2026-11-13T16:00:00.000000+00:00", description="中国14日零点")
    result = items(client, start_date="2026-11-13", end_date="2026-11-13")
    assert result["total"] == 3
    assert {i["description"] for i in result["items"]} == {"中国13日零点", "中国13日零点无小数", "中国13日最后一刻"}
    assert items(client, start_date="2026-11-13")["total"] == 4
    assert items(client, end_date="2026-11-13")["total"] == 4


def test_actor_and_date_filters_combine_and_actor_choices_do_not_shrink(client):
    wanted = record(at="2026-11-13T02:00:00.000000+00:00", description="甲目标日")
    record(at="2026-11-14T02:00:00.000000+00:00", description="甲其他日")
    record(user_id="operator-b", description="乙目标日")
    result = items(client, actor_id="operator-a", start_date="2026-11-13", end_date="2026-11-13")
    assert result["total"] == 1
    assert result["items"][0]["id"] == wanted
    assert {a["id"] for a in result["actors"]} == {"operator-a", "operator-b"}


def test_renamed_actor_is_one_stable_choice_and_both_names_filter_together(client):
    record(actor="很久以前的名字")
    record(actor="中间的名字")
    result = items(client, actor_id="operator-a")
    assert result["total"] == 2
    assert result["actors"] == [{"id": "operator-a", "name": "新运营甲"}]
    assert {i["actor"] for i in result["items"]} == {"很久以前的名字", "中间的名字"}


def test_historical_duplicate_names_do_not_merge_identities_or_require_team_members(client):
    # Some legacy restores can retain audit history after removing a user.
    # The activity API must preserve these IDs and their last recorded names.
    with sqlite3.connect(backend.DB_PATH) as conn:
        for identity in ("former-a", "former-b"):
            conn.execute("INSERT INTO activity(user_id,actor,action,description,created_at) VALUES(?,?,?,?,?)",
                         (identity, "同名运营", "update", identity, "2026-11-13T01:00:00.000000+00:00"))
    result = items(client, actor_id="former-a")
    assert result["total"] == 1
    assert result["items"][0]["description"] == "former-a"
    assert {a["id"] for a in result["actors"] if a["name"] == "同名运营"} == {"former-a", "former-b"}


def test_system_log_has_filterable_identity(client):
    record(user_id=None, actor="系统", description="自动备份")
    record()
    result = items(client, actor_id="__system__")
    assert result["total"] == 1
    assert result["items"][0]["description"] == "自动备份"
    assert {"id": "__system__", "name": "系统"} in result["actors"]


def test_stable_pagination_uses_timestamp_then_id_and_total_not_page_size(client):
    ids = [record(description=str(i)) for i in range(4)]
    record(at="2026-11-01T01:00:00.000000+00:00", description="晚插入旧事件")
    first = items(client, limit=2)
    second = items(client, limit=2, offset=2)
    last = items(client, limit=2, offset=4)
    assert first["total"] == second["total"] == last["total"] == 5
    assert [i["id"] for i in first["items"] + second["items"]] == ids[::-1]
    assert last["items"][0]["description"] == "晚插入旧事件"
    assert items(client, limit=2, offset=99)["items"] == []


def test_empty_result_keeps_all_actor_choices_and_empty_library_is_empty(client):
    assert items(client) == {"items": [], "total": 0, "actors": []}
    record()
    result = items(client, start_date="2027-01-01")
    assert result["items"] == [] and result["total"] == 0
    assert result["actors"] == [{"id": "operator-a", "name": "新运营甲"}]


def test_injection_string_is_value_not_sql(client):
    record()
    result = items(client, actor_id="operator-a' OR 1=1 --")
    assert result["items"] == [] and result["total"] == 0
    assert items(client)["total"] == 1


@pytest.mark.parametrize("params", [
    {"start_date": "2026-2-03"}, {"end_date": "2026-02-30"},
    {"start_date": "not-a-date"}, {"end_date": "2026-11-13T12:00:00"},
    {"start_date": "2026-11-14", "end_date": "2026-11-13"},
    {"start_date": "0001-01-01"}, {"end_date": "9999-12-31"},
])
def test_invalid_dates_return_400(client, params):
    assert client.get("/api/activity", params=params).status_code == 400


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 201}, {"offset": -1}])
def test_pagination_limits_are_validated(client, params):
    assert client.get("/api/activity", params=params).status_code == 422


def test_undo_permission_marker_and_activity_auth_are_preserved(client):
    with backend._connection(write=True) as conn:
        conn.execute("INSERT INTO batches VALUES(?,?,?,0)", ("batch-a", "operator-a", backend.now()))
        conn.execute("INSERT INTO batches VALUES(?,?,?,1)", ("batch-b", "operator-a", backend.now()))
        stamp = backend.now()
        conn.execute("""INSERT INTO products(id,code,name,owner,created_by,batch_id,created_at,updated_at)
                        VALUES(?,?,?,?,?,?,?,?)""",
                     ("batch-a-product", "TEST-UNDO-1", "实际批次商品", "新运营甲", "operator-a", "batch-a", stamp, stamp))
    record(action="import", batch_id="batch-a", description="可撤销")
    record(action="import", batch_id="batch-b", description="已撤销")
    result = items(client, actor_id="operator-a")
    assert {i["description"]: i["can_undo"] for i in result["items"]} == {"可撤销": True, "已撤销": False}
    client.post("/api/logout")
    assert client.get("/api/activity").status_code == 401
    assert client.post("/api/login", json={"name": "新运营乙", "password": "activity-test-password"}).status_code == 200
    assert not any(i["can_undo"] for i in items(client)["items"])
