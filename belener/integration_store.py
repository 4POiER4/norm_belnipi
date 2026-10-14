"""External sites with credentials (Стройдок and others)."""

from __future__ import annotations

import logging
import os
import re
from typing import Any
from uuid import UUID

log = logging.getLogger(__name__)

_DB_GETTER = None
_STN_HOST = re.compile(r"stn\.by", re.I)
_TNPA_HOST = re.compile(r"tnpa\.by", re.I)
_NORMACS_HOST = re.compile(r"normacs|nanocad\.ru", re.I)


def bind_db_getter(getter) -> None:
    global _DB_GETTER
    _DB_GETTER = getter


def _encrypt(value: str) -> str:
    from belener.settings_store import _encrypt as enc

    return enc(value)


def _decrypt(value: str) -> str:
    from belener.settings_store import _decrypt as dec

    return dec(value)


def _legacy_stn_password() -> str:
    from belener.settings_store import get_setting

    return (
        get_setting("stn.password")
        or (os.environ.get("PDF_STN_PASSWORD") or os.environ.get("STN_PASSWORD") or "").strip()
    )


def _legacy_stn_login() -> str:
    from belener.settings_store import get_setting

    return (
        get_setting("stn.login")
        or (os.environ.get("PDF_STN_LOGIN") or os.environ.get("STN_LOGIN") or "").strip()
    )


def _effective_stn_password(site_login: str, decrypted_password: str, password_enc: str) -> str:
    if decrypted_password:
        return decrypted_password
    fallback = _legacy_stn_password()
    login = (site_login or "").strip()
    fallback_login = _legacy_stn_login()
    if fallback and (not login or login == fallback_login):
        if password_enc and not decrypted_password:
            log.warning(
                "integration_store: STN password in DB could not be decrypted — using .env fallback"
            )
        return fallback
    if password_enc and not decrypted_password:
        log.warning(
            "integration_store: STN password decrypt failed and login does not match .env"
        )
    return ""


def _row_to_dict(row: dict) -> dict[str, Any]:
    enc = str(row.get("password_enc") or "")
    pwd = _decrypt(enc) if enc else ""
    site_login = row.get("login_name") or ""
    kind = row.get("kind") or "generic"
    site_url = str(row.get("site_url") or "")
    is_stn = kind == "stn" or bool(_STN_HOST.search(site_url))
    is_tnpa = kind == "tnpa" or bool(_TNPA_HOST.search(site_url))
    is_normacs = kind == "normacs" or bool(_NORMACS_HOST.search(site_url))
    effective_pwd = pwd if pwd else (_effective_stn_password(site_login, pwd, enc) if is_stn else "")
    return {
        "id": str(row["id"]),
        "name": row.get("name") or "",
        "site_url": site_url,
        "login": site_login,
        "password_set": bool(effective_pwd) or (bool(enc) if is_stn else False),
        "password_hint": _mask(pwd or effective_pwd) if is_stn else "",
        "kind": kind,
        "can_test": is_stn or is_tnpa or is_normacs,
    }


def _mask(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 2:
        return "••"
    return value[:2] + "•" * min(8, len(value) - 2)


def _detect_kind(site_url: str, kind: str = "") -> str:
    k = (kind or "").strip().lower()
    if k in ("stn", "tnpa", "normacs", "generic"):
        return k
    if _STN_HOST.search(site_url or ""):
        return "stn"
    if _TNPA_HOST.search(site_url or ""):
        return "tnpa"
    if _NORMACS_HOST.search(site_url or ""):
        return "normacs"
    return "generic"


def get_normacs_credentials() -> dict[str, str]:
    try:
        for site in list_sites():
            if site.get("kind") == "normacs" or _NORMACS_HOST.search(site.get("site_url") or ""):
                full = _get_site_secrets(site["id"])
                if full and full.get("login") and full.get("password"):
                    return full
    except Exception:
        log.warning("NormaCS: cannot read integrations; using environment")
    return {
        "base_url": (os.environ.get("PDF_NORMACS_BASE_URL")
                     or "http://n.nanocad.ru:9999/normacsweb/").strip(),
        "login": (os.environ.get("PDF_NORMACS_LOGIN") or "").strip(),
        "password": (os.environ.get("PDF_NORMACS_PASSWORD") or "").strip(),
    }


def list_sites() -> list[dict[str, Any]]:
    if _DB_GETTER is None:
        return []
    with _DB_GETTER() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, name, site_url, login_name, password_enc, kind FROM tenant_integrations ORDER BY created_at"
            )
            rows = cur.fetchall()
    return [_row_to_dict(dict(r)) for r in rows]


def get_site(site_id: str) -> dict[str, Any] | None:
    if _DB_GETTER is None:
        return None
    with _DB_GETTER() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, name, site_url, login_name, password_enc, kind FROM tenant_integrations WHERE id = %s",
                (site_id,),
            )
            row = cur.fetchone()
    return _row_to_dict(dict(row)) if row else None


def get_stn_credentials() -> dict[str, str]:
    """Credentials for normy.stn.by — из таблицы сайтов или legacy tenant_settings."""
    for site in list_sites():
        if site.get("kind") == "stn" or _STN_HOST.search(site.get("site_url") or ""):
            full = _get_site_secrets(site["id"])
            if full and (full.get("password") or "").strip():
                return full
    from belener.settings_store import get_setting

    login = get_setting("stn.login")
    password = get_setting("stn.password")
    if login or password:
        return {
            "base_url": get_setting("stn.base_url") or "https://normy.stn.by",
            "login": login,
            "password": password,
        }
    env_login = (os.environ.get("PDF_STN_LOGIN") or os.environ.get("STN_LOGIN") or "").strip()
    env_password = (os.environ.get("PDF_STN_PASSWORD") or os.environ.get("STN_PASSWORD") or "").strip()
    if env_login or env_password:
        return {
            "base_url": (os.environ.get("PDF_STN_BASE_URL") or "https://normy.stn.by").strip().rstrip("/"),
            "login": env_login,
            "password": env_password,
        }
    return {"base_url": "", "login": "", "password": ""}


def get_tnpa_credentials() -> dict[str, str]:
    """URL tnpa.by из таблицы сайтов или из .env."""
    for site in list_sites():
        if site.get("kind") == "tnpa" or _TNPA_HOST.search(site.get("site_url") or ""):
            base_url = (site.get("site_url") or "").strip().rstrip("/")
            return {"base_url": base_url or "https://tnpa.by"}
    base = (os.environ.get("PDF_TNPA_BASE_URL") or "https://tnpa.by").strip().rstrip("/")
    return {"base_url": base}


def _get_site_secrets(site_id: str) -> dict[str, str] | None:
    if _DB_GETTER is None:
        return None
    with _DB_GETTER() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT site_url, login_name, password_enc, kind FROM tenant_integrations WHERE id = %s",
                (site_id,),
            )
            row = cur.fetchone()
    if not row:
        return None
    enc = str(row["password_enc"] or "")
    pwd = _decrypt(enc) if enc else ""
    login = (row["login_name"] or "").strip()
    base_url = (row["site_url"] or "").strip().rstrip("/")
    kind = row.get("kind") or ""
    if not pwd and (_detect_kind(base_url, kind) == "stn" or _STN_HOST.search(base_url)):
        pwd = _effective_stn_password(login, pwd, enc)
    return {
        "base_url": base_url,
        "login": login,
        "password": pwd,
    }


def resolve_site_credentials(site_id: str) -> dict[str, str] | None:
    """Учётные данные сайта с учётом legacy/.env для Стройдok."""
    return _get_site_secrets(site_id)


def create_site(
    *,
    name: str,
    site_url: str,
    login: str = "",
    password: str = "",
    kind: str = "",
    updated_by: str = "",
) -> dict[str, Any]:
    if _DB_GETTER is None:
        raise RuntimeError("integration_store: DB not bound")
    url = (site_url or "").strip()
    k = _detect_kind(url, kind)
    with _DB_GETTER() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO tenant_integrations (name, site_url, login_name, password_enc, kind, updated_by)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id, name, site_url, login_name, password_enc, kind
                """,
                (
                    (name or "").strip() or url,
                    url,
                    (login or "").strip(),
                    _encrypt(password or ""),
                    k,
                    updated_by or None,
                ),
            )
            row = dict(cur.fetchone())
        conn.commit()
    _reset_stn()
    return _row_to_dict(row)


def update_site(
    site_id: str,
    *,
    name: str,
    site_url: str,
    login: str = "",
    password: str | None = None,
    kind: str = "",
    updated_by: str = "",
) -> dict[str, Any] | None:
    if _DB_GETTER is None:
        raise RuntimeError("integration_store: DB not bound")
    existing = get_site(site_id)
    if not existing:
        return None
    url = (site_url or "").strip()
    k = _detect_kind(url, kind or existing.get("kind") or "")
    pwd_enc = None
    if password is not None and password != "":
        pwd_enc = _encrypt(password)
    with _DB_GETTER() as conn:
        with conn.cursor() as cur:
            if pwd_enc is not None:
                cur.execute(
                    """
                    UPDATE tenant_integrations SET
                      name = %s, site_url = %s, login_name = %s, password_enc = %s,
                      kind = %s, updated_by = %s, updated_at = NOW()
                    WHERE id = %s
                    RETURNING id, name, site_url, login_name, password_enc, kind
                    """,
                    ((name or "").strip(), url, (login or "").strip(), pwd_enc, k, updated_by or None, site_id),
                )
            else:
                cur.execute(
                    """
                    UPDATE tenant_integrations SET
                      name = %s, site_url = %s, login_name = %s,
                      kind = %s, updated_by = %s, updated_at = NOW()
                    WHERE id = %s
                    RETURNING id, name, site_url, login_name, password_enc, kind
                    """,
                    ((name or "").strip(), url, (login or "").strip(), k, updated_by or None, site_id),
                )
            row = cur.fetchone()
        conn.commit()
    _reset_stn()
    return _row_to_dict(dict(row)) if row else None


def delete_site(site_id: str) -> bool:
    if _DB_GETTER is None:
        return False
    with _DB_GETTER() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM tenant_integrations WHERE id = %s", (site_id,))
            deleted = cur.rowcount > 0
        conn.commit()
    if deleted:
        _reset_stn()
    return deleted


def migrate_legacy_stn() -> None:
    """Перенос stn.* из tenant_settings и .env в tenant_integrations."""
    if _DB_GETTER is None:
        return
    with _DB_GETTER() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM tenant_integrations")
            if int(cur.fetchone()["n"]) > 0:
                return
    from belener.settings_store import get_setting

    login = get_setting("stn.login")
    password = get_setting("stn.password")
    base = get_setting("stn.base_url")
    if not login and not password:
        login = (os.environ.get("PDF_STN_LOGIN") or os.environ.get("STN_LOGIN") or "").strip()
        password = (os.environ.get("PDF_STN_PASSWORD") or os.environ.get("STN_PASSWORD") or "").strip()
        base = (os.environ.get("PDF_STN_BASE_URL") or "https://normy.stn.by").strip()
    if not login and not password:
        return
    create_site(
        name="Стройдок (normy.stn.by)",
        site_url=base or "https://normy.stn.by",
        login=login,
        password=password,
        kind="stn",
        updated_by="migrate",
    )
    log.info("integration_store: migrated STN credentials to tenant_integrations")


def _reset_stn() -> None:
    try:
        from belener.stn_lookup import reset_stn_client

        reset_stn_client()
    except Exception:
        pass
