"""Server-side company roles without rewriting legacy account records.

The first account owns the workspace.  Role defaults may be narrowed or extended
per member, except operations permanently reserved to the sole owner.
"""
from __future__ import annotations

from contextlib import nullcontext
from typing import Any

from fastapi import HTTPException

OWNER_KEY = "permissions:owner_id"

_GROUPS = (
    ("商品", (
        ("products.create", "创建商品"), ("products.import", "导入商品"),
        ("products.edit_own", "编辑本人或负责商品"), ("products.edit_all", "编辑全库商品"),
        ("products.delete_own", "移除本人或负责商品"), ("products.delete_all", "移除全库商品"),
        ("products.restore_own", "恢复本人或负责商品"), ("products.restore_all", "恢复全库商品"),
        ("products.merge", "人工合并商品"), ("products.purge", "永久清理商品回收站"))),
    ("企业知识", (("knowledge.create", "新增企业知识"), ("knowledge.edit_own", "编辑本人知识"),
                  ("knowledge.edit_all", "编辑全部企业知识"), ("knowledge.archive", "停用与恢复企业知识"),
                  ("knowledge.purge", "永久清理企业知识"))),
    ("账号与系统", (("team.create", "添加员工账号"), ("team.manage", "管理成员与授权"),
                    ("settings.ai", "配置模型与资料发送"), ("settings.price", "配置手动采价"),
                    ("data.backup", "导出完整数据备份"), ("audit.all", "查看全员动态"))),
    ("AI 使用", (("ai.use", "使用 AI 功能"), ("ai.web", "使用联网搜索"))),
    ("公司公告", (("announcements.edit", "创建和编辑公告"), ("announcements.publish", "发布与撤回公告"),
                  ("announcements.archive", "停用与恢复公告"))),
)
OWNER_ONLY = frozenset({"team.manage", "products.purge", "knowledge.purge"})
CATALOG = [{"key": key, "label": label, "group": group, "owner_only": key in OWNER_ONLY}
           for group, entries in _GROUPS for key, label in entries]
KEYS = frozenset(item["key"] for item in CATALOG)
KNOWLEDGE_KEYS = frozenset(key for key in KEYS if key.startswith("knowledge."))
DEFAULTS = {
    "owner": KEYS,
    "admin": frozenset({"products.create", "products.import", "products.edit_own", "products.edit_all",
                         "products.delete_own", "products.delete_all", "products.restore_own", "products.restore_all",
                         "products.merge", "knowledge.create", "knowledge.edit_own", "knowledge.edit_all",
                         "team.create", "settings.price", "ai.use", "ai.web", "audit.all",
                         "announcements.edit", "announcements.publish"}),
    "operator": frozenset({"products.create", "products.import", "products.edit_own", "products.delete_own",
                            "products.restore_own", "ai.use", "ai.web"}),
}


def _connection(conn=None, *, write=False):
    if conn is not None:
        return nullcontext(conn)
    import backend
    return backend._connection(write=write)


def owner_id(conn=None) -> str | None:
    with _connection(conn) as connection:
        row = connection.execute("SELECT value FROM app_meta WHERE key=?", (OWNER_KEY,)).fetchone()
        return row["value"] if row else None


def init_storage(conn=None) -> None:
    """Add only new tables/meta; preserve all five original users columns."""
    with _connection(conn, write=True) as connection:
        # Do not executescript here: it would commit the caller's transaction.
        connection.execute("""CREATE TABLE IF NOT EXISTS account_access (
            user_id TEXT PRIMARY KEY REFERENCES users(id), disabled INTEGER NOT NULL DEFAULT 0 CHECK(disabled IN (0,1)),
            revision INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL DEFAULT '')""")
        connection.execute("""CREATE TABLE IF NOT EXISTS permission_overrides (
            user_id TEXT NOT NULL REFERENCES users(id), permission_key TEXT NOT NULL,
            allowed INTEGER NOT NULL CHECK(allowed IN (0,1)), PRIMARY KEY(user_id,permission_key))""")
        if owner_id(connection) is None:
            first = connection.execute("SELECT id FROM users ORDER BY created_at,id LIMIT 1").fetchone()
            if first:
                connection.execute("INSERT INTO app_meta(key,value) VALUES(?,?)", (OWNER_KEY, first["id"]))


def establish_owner(conn, identity: str) -> None:
    """Called in the same transaction that creates the first account."""
    assigned = owner_id(conn)
    if assigned is not None:
        if assigned != identity:
            raise HTTPException(409, "工作空间已有唯一老板，不能创建第二位老板。")
        return
    first = conn.execute("SELECT id FROM users ORDER BY created_at,id LIMIT 1").fetchone()
    if not first or first["id"] != identity:
        raise HTTPException(409, "只有首位使用者能够建立老板身份。")
    conn.execute("INSERT INTO app_meta(key,value) VALUES(?,?)", (OWNER_KEY, identity))


def is_owner(user: dict | None, conn=None) -> bool:
    return bool(user and isinstance(user.get("id"), str) and not user.get("disabled", False)
                and user["id"] == owner_id(conn))


def public_user(conn, row, *, overrides=False) -> dict:
    identity = row["id"]
    access = conn.execute("SELECT disabled,revision FROM account_access WHERE user_id=?", (identity,)).fetchone()
    disabled = bool(access["disabled"]) if access else False
    owner = identity == owner_id(conn)
    role = "owner" if owner else row["role"] if row["role"] in {"admin", "operator"} else "operator"
    adjustments = {item["permission_key"]: bool(item["allowed"]) for item in conn.execute(
        "SELECT permission_key,allowed FROM permission_overrides WHERE user_id=?", (identity,))
                   if item["permission_key"] in KEYS and item["permission_key"] not in OWNER_ONLY}
    allowed = set(DEFAULTS[role])
    if not owner:
        for key, value in adjustments.items():
            if value:
                allowed.add(key)
            else:
                allowed.discard(key)
    if role == "operator":
        allowed.difference_update(KNOWLEDGE_KEYS)
        adjustments = {key: value for key, value in adjustments.items() if key not in KNOWLEDGE_KEYS}
    if disabled:
        allowed.clear()
    result = {"id": identity, "name": row["name"], "role": role, "is_owner": owner,
              "permissions": sorted(allowed), "disabled": disabled,
              "revision": int(access["revision"]) if access else 0}
    if "created_at" in row.keys():
        result["created_at"] = row["created_at"]
    if overrides:
        result["permission_overrides"] = adjustments if not owner else {}
    return result


def has(user: dict | None, key: str) -> bool:
    if key not in KEYS or not user or user.get("disabled", False):
        return False
    # Canonical ownership, never a client-supplied role name, controls reserved
    # operations. Authenticated requests receive freshly derived permissions.
    if is_owner(user):
        return True
    if key in OWNER_ONLY:
        return False
    if user.get("role") not in {"admin", "operator"}:
        return False
    if user.get("role") == "operator" and key in KNOWLEDGE_KEYS:
        return False
    allowed = user.get("permissions")
    return key in allowed if isinstance(allowed, list) else key in DEFAULTS[user["role"]]


def require(user: dict | None, key: str) -> None:
    if not has(user, key):
        raise HTTPException(403, "老板尚未授予此操作权限。")


def can_product(user: dict | None, product, action: str = "edit") -> bool:
    if action not in {"edit", "delete", "restore"}:
        return False
    if has(user, f"products.{action}_all"):
        return True
    if not has(user, f"products.{action}_own"):
        return False
    return bool(user and (product["created_by"] == user["id"] or product["owner"] == user["name"]))


def require_product(user: dict | None, product, action: str = "edit") -> None:
    if not can_product(user, product, action):
        raise HTTPException(403, "没有操作此商品的权限；可请老板调整本人或全库商品权限。")


def validate_overrides(raw: Any, role: str | None = None) -> dict[str, bool]:
    if not isinstance(raw, dict) or len(raw) > len(KEYS):
        raise HTTPException(400, "成员授权需为权限名称与开关组成的对象。")
    if any(not isinstance(key, str) or key not in KEYS or type(value) is not bool for key, value in raw.items()):
        raise HTTPException(400, "包含未知权限或非布尔开关。")
    if set(raw) & OWNER_ONLY:
        raise HTTPException(400, "成员管理与永久清理只属于唯一老板，不能转授。")
    if role == "operator" and any(raw.get(key) is True for key in KNOWLEDGE_KEYS):
        raise HTTPException(400, "企业知识只能由老板或获授权的管理员维护；员工不能获得知识修改权限。")
    return dict(raw)


def catalog() -> dict:
    return {"items": CATALOG, "roles": {role: sorted(keys) for role, keys in DEFAULTS.items()}, "owner_id": owner_id()}
