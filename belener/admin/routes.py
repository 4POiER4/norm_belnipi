"""Admin subsite: AD login and tenant settings."""

from __future__ import annotations

import os
from functools import wraps

from flask import Blueprint, jsonify, request, send_from_directory, session

from belener.admin_auth import (
    admin_usernames,
    check_ldap_auth,
    clear_user_cache,
    load_ad_config,
    normalize_ad_username,
    test_ldap_connection,
    user_is_admin,
)
from belener.settings_store import get_setting, is_ad_configured, set_many

admin_bp = Blueprint("admin", __name__, static_folder=None)
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _setup_open() -> bool:
    """Первоначальная настройка доступна, пока AD не подключён."""
    return not is_ad_configured()


def _require_setup_or_admin(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if is_ad_configured():
            return _require_admin(fn)(*args, **kwargs)
        if not _setup_open():
            return jsonify({"error": "Первоначальная настройка недоступна"}), 403
        return fn(*args, **kwargs)

    return wrapper


def _admin_logged_in() -> bool:
    return bool(session.get("admin_logged_in")) and bool(session.get("admin_username"))


def _require_admin(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not _admin_logged_in():
            return jsonify({"error": "Требуется вход в админку"}), 401
        login = normalize_ad_username(session.get("admin_username") or "")
        groups = session.get("admin_groups") or []
        if not user_is_admin(login, groups):
            return jsonify({"error": "Недостаточно прав администратора"}), 403
        return fn(*args, **kwargs)

    return wrapper


def _audit(action: str, detail: str = "") -> None:
    from belener.settings_store import _DB_GETTER

    if _DB_GETTER is None:
        return
    user = normalize_ad_username(session.get("admin_username") or "setup")
    try:
        with _DB_GETTER() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO admin_audit_log (username, action, detail) VALUES (%s, %s, %s)",
                    (user or None, action, detail[:2000] if detail else None),
                )
            conn.commit()
    except Exception:
        pass


def _mask_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 2:
        return "••"
    return value[:2] + "•" * min(8, len(value) - 2)


@admin_bp.route("/")
def admin_index():
    return send_from_directory(os.path.join(ROOT_DIR, "admin"), "index.html")


@admin_bp.route("/api/bootstrap")
def admin_bootstrap():
    configured = is_ad_configured()
    return jsonify(
        {
            "ad_configured": configured,
            "setup_allowed": not configured,
            "logged_in": _admin_logged_in(),
            "username": normalize_ad_username(session.get("admin_username") or ""),
            "display_name": (session.get("admin_display_name") or "").strip(),
            "integrations": [],
        }
    )


@admin_bp.route("/api/me")
def admin_me():
    login = normalize_ad_username(session.get("admin_username") or "")
    groups = session.get("admin_groups") or []
    return jsonify(
        {
            "logged_in": _admin_logged_in(),
            "username": login,
            "display_name": (session.get("admin_display_name") or login).strip(),
            "is_admin": user_is_admin(login, groups) if login else False,
            "ad_configured": is_ad_configured(),
        }
    )


@admin_bp.route("/api/login", methods=["POST"])
def admin_login():
    if not is_ad_configured():
        return jsonify({"error": "Сначала выполните первоначальную настройку AD"}), 400
    data = request.get_json(silent=True) or {}
    username = normalize_ad_username(data.get("username") or "")
    password = data.get("password") or ""
    ok, err, meta = check_ldap_auth(username, password)
    if not ok:
        return jsonify({"error": err}), 401
    groups = meta.get("groups") or []
    if not user_is_admin(username, groups):
        return jsonify({"error": "У вас нет прав администратора"}), 403
    session.permanent = True
    session["admin_logged_in"] = True
    session["admin_username"] = username
    session["admin_display_name"] = meta.get("display_name") or username
    session["admin_groups"] = groups
    _audit("login", username)
    return jsonify({"success": True, "username": username, "display_name": session["admin_display_name"]})


@admin_bp.route("/api/logout", methods=["POST"])
def admin_logout():
    user = normalize_ad_username(session.get("admin_username") or "")
    clear_user_cache(user)
    session.pop("admin_logged_in", None)
    session.pop("admin_username", None)
    session.pop("admin_display_name", None)
    session.pop("admin_groups", None)
    return jsonify({"success": True})


@admin_bp.route("/api/setup", methods=["POST"])
@_require_setup_or_admin
def admin_setup():
    data = request.get_json(silent=True) or {}
    ad = data.get("ad") or {}
    admin_users = data.get("admin_users") or ad.get("admin_users") or ""

    items = [
        {"key": "ad.uri", "value": (ad.get("uri") or "").strip(), "category": "ad", "label": "LDAP URI"},
        {"key": "ad.base_dn", "value": (ad.get("base_dn") or "").strip(), "category": "ad", "label": "Base DN"},
        {"key": "ad.bind_dn", "value": (ad.get("bind_dn") or "").strip(), "category": "ad", "label": "Bind DN"},
        {
            "key": "ad.bind_password",
            "value": ad.get("bind_password") or "",
            "is_secret": True,
            "category": "ad",
            "label": "Bind password",
        },
        {
            "key": "ad.user_attr",
            "value": (ad.get("user_attr") or "sAMAccountName").strip(),
            "category": "ad",
            "label": "User attribute",
        },
        {
            "key": "ad.admin_users",
            "value": (admin_users or "").strip(),
            "category": "ad",
            "label": "Admin users",
        },
        {
            "key": "ad.admin_group",
            "value": (ad.get("admin_group") or "").strip(),
            "category": "ad",
            "label": "Admin AD group",
        },
    ]

    cfg = {
        "uri": items[0]["value"],
        "base": items[1]["value"],
        "bind_dn": items[2]["value"],
        "bind_password": items[3]["value"] or get_setting("ad.bind_password"),
    }
    ok, msg = test_ldap_connection(cfg)
    if not ok:
        return jsonify({"error": msg}), 400

    set_many(items, updated_by="setup")
    _audit("setup", f"ad={cfg['uri']}")
    return jsonify({"success": True, "message": "Active Directory подключён. Войдите под своей учётной записью."})


@admin_bp.route("/api/settings/ad", methods=["GET"])
@_require_admin
def admin_get_ad():
    cfg = load_ad_config()
    return jsonify(
        {
            "uri": cfg.get("uri") or "",
            "base_dn": cfg.get("base") or "",
            "bind_dn": cfg.get("bind_dn") or "",
            "bind_password_set": bool(cfg.get("bind_password")),
            "bind_password_hint": _mask_secret(cfg.get("bind_password") or ""),
            "user_attr": cfg.get("user_attr") or "sAMAccountName",
            "admin_users": cfg.get("admin_users") or "",
            "admin_group": cfg.get("admin_group") or "",
            "admin_usernames": sorted(admin_usernames(cfg)),
        }
    )


@admin_bp.route("/api/settings/ad", methods=["PUT"])
@_require_admin
def admin_put_ad():
    data = request.get_json(silent=True) or {}
    bind_password = data.get("bind_password")
    if bind_password in (None, ""):
        bind_password = get_setting("ad.bind_password")

    cfg = {
        "uri": (data.get("uri") or "").strip(),
        "base": (data.get("base_dn") or "").strip(),
        "bind_dn": (data.get("bind_dn") or "").strip(),
        "bind_password": bind_password or "",
    }
    ok, msg = test_ldap_connection(cfg)
    if not ok:
        return jsonify({"error": msg}), 400

    items = [
        {"key": "ad.uri", "value": cfg["uri"], "category": "ad", "label": "LDAP URI"},
        {"key": "ad.base_dn", "value": cfg["base"], "category": "ad", "label": "Base DN"},
        {"key": "ad.bind_dn", "value": cfg["bind_dn"], "category": "ad", "label": "Bind DN"},
        {
            "key": "ad.bind_password",
            "value": cfg["bind_password"],
            "is_secret": True,
            "category": "ad",
            "label": "Bind password",
        },
        {
            "key": "ad.user_attr",
            "value": (data.get("user_attr") or "sAMAccountName").strip(),
            "category": "ad",
            "label": "User attribute",
        },
        {
            "key": "ad.admin_users",
            "value": (data.get("admin_users") or "").strip(),
            "category": "ad",
            "label": "Admin users",
        },
        {
            "key": "ad.admin_group",
            "value": (data.get("admin_group") or "").strip(),
            "category": "ad",
            "label": "Admin AD group",
        },
    ]
    set_many(items, updated_by=normalize_ad_username(session.get("admin_username") or ""))
    _audit("update_ad", cfg["uri"])
    return jsonify({"success": True})


@admin_bp.route("/api/settings/ad/test", methods=["POST"])
@_require_setup_or_admin
def admin_test_ad():
    data = request.get_json(silent=True) or {}
    bind_password = data.get("bind_password")
    if bind_password in (None, ""):
        bind_password = get_setting("ad.bind_password")
    cfg = {
        "uri": (data.get("uri") or get_setting("ad.uri")).strip(),
        "base": (data.get("base_dn") or get_setting("ad.base_dn")).strip(),
        "bind_dn": (data.get("bind_dn") or get_setting("ad.bind_dn")).strip(),
        "bind_password": bind_password or "",
    }
    ok, msg = test_ldap_connection(cfg)
    return jsonify({"success": ok, "message": msg}), (200 if ok else 400)


@admin_bp.route("/api/settings/sites", methods=["GET"])
@_require_admin
def admin_list_sites():
    from belener.integration_store import list_sites

    return jsonify({"sites": list_sites()})


@admin_bp.route("/api/settings/sites", methods=["POST"])
@_require_admin
def admin_create_site():
    from belener.integration_store import create_site

    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    site_url = (data.get("site_url") or "").strip()
    if not name or not site_url:
        return jsonify({"error": "Укажите название и адрес сайта"}), 400
    site = create_site(
        name=name,
        site_url=site_url,
        login=(data.get("login") or "").strip(),
        password=data.get("password") or "",
        kind=(data.get("kind") or "").strip(),
        updated_by=normalize_ad_username(session.get("admin_username") or ""),
    )
    _audit("create_site", name)
    return jsonify({"success": True, "site": site})


@admin_bp.route("/api/settings/sites/<site_id>", methods=["PUT"])
@_require_admin
def admin_update_site(site_id: str):
    from belener.integration_store import update_site

    data = request.get_json(silent=True) or {}
    password = data.get("password")
    site = update_site(
        site_id,
        name=(data.get("name") or "").strip(),
        site_url=(data.get("site_url") or "").strip(),
        login=(data.get("login") or "").strip(),
        password=password if password not in (None, "") else None,
        kind=(data.get("kind") or "").strip(),
        updated_by=normalize_ad_username(session.get("admin_username") or ""),
    )
    if not site:
        return jsonify({"error": "Сайт не найден"}), 404
    _audit("update_site", site_id)
    return jsonify({"success": True, "site": site})


@admin_bp.route("/api/settings/sites/<site_id>", methods=["DELETE"])
@_require_admin
def admin_delete_site(site_id: str):
    from belener.integration_store import delete_site

    if not delete_site(site_id):
        return jsonify({"error": "Сайт не найден"}), 404
    _audit("delete_site", site_id)
    return jsonify({"success": True})


@admin_bp.route("/api/settings/sites/<site_id>/test", methods=["POST"])
@_require_admin
def admin_test_site(site_id: str):
    from belener.integration_store import get_site, resolve_site_credentials
    from belener.stn_lookup import StnClient, reset_stn_client
    from belener.tnpa_lookup import test_tnpa_connection

    site = get_site(site_id)
    if not site:
        return jsonify({"error": "Сайт не найден"}), 404
    if not site.get("can_test"):
        return jsonify({"error": "Проверка доступна для Стройдок, NormaCS и ТНПА"}), 400

    kind = (site.get("kind") or "").strip().lower()
    site_url = (site.get("site_url") or "").strip()
    if kind == "tnpa" or "tnpa.by" in site_url.casefold():
        ok, message = test_tnpa_connection(base_url=site_url or None)
        return jsonify({"success": ok, "message": message}), (200 if ok else 400)

    creds = resolve_site_credentials(site_id)
    if not creds or not creds.get("login") or not creds.get("password"):
        return jsonify({"error": "Укажите логин и пароль"}), 400
    if kind == "normacs" or "normacs" in site_url.casefold() or "nanocad.ru" in site_url.casefold():
        from belener.normacs_lookup import NormacsClient, NormacsError
        client = NormacsClient(**creds)
        try:
            client.login()
            rows = client.search("ГОСТ 21105-87")
            return jsonify({"success": True, "message": f"NormaCS: вход выполнен; совпадений: {len(rows)}"})
        except NormacsError as exc:
            return jsonify({"success": False, "message": str(exc)}), 400
        finally:
            client.close()
    reset_stn_client()
    client = StnClient(base_url=creds.get("base_url") or None, login=creds["login"], password=creds.get("password") or "")
    try:
        client.login(client._login_user, client._login_pass)
        return jsonify({"success": True, "message": "Вход выполнен успешно"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 400

