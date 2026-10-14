"""Поиск обозначений и статусов через API веб-интерфейса NormaCS."""

from __future__ import annotations

import http.cookiejar
import json
import logging
import re
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any, Callable

from belener.config import (normacs_credentials, normacs_lookup_enabled, normacs_timeout_sec,
                            normacs_retries, normacs_document_budget_sec)

log = logging.getLogger(__name__)
DEFAULT_STATUSES = {
    "1": "Действует", "2": "Частично действует", "0": "Не действует",
    "4": "Проект", "8": "Неопределенный статус",
}


class NormacsError(RuntimeError):
    """Ошибка подключения, сессии или формата ответа NormaCS."""

    def __init__(self, message: str, *, code: str = "error", retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class NormacsDeadline(TimeoutError):
    pass


def designation_key(value: str) -> str:
    """Тип/номер/год без OCR-подмен; 97 и 1997 обозначают тот же год."""
    value = re.sub(r"[–—−‑]", "-", str(value or "")).upper()
    value = re.sub(r"\s+", "", value)
    value = re.sub(r"\((?:\d{4,6})\)$", "", value)
    return re.sub(r"-(?:19|20)(\d{2})$", r"-\1", value)


def normacs_query(kind: str, ref: str) -> str:
    value = re.sub(r"\s+", " ", str(ref or "").strip())
    value = re.sub(r"[–—−‑]", "-", value)
    value = re.sub(r"\s*([.\-])\s*", r"\1", value)
    return value if value.casefold().startswith(kind.casefold()) else f"{kind} {value}".strip()


def _matching_documents(query: str, rows: list[dict]) -> list[dict]:
    key = designation_key(query)
    exact = [r for r in rows if designation_key(r.get("designation", "")) == key
             and not r.get("isChange")]
    if exact:
        return exact
    # Без года допускаем только единственный номер; несколько редакций неоднозначны.
    if not re.search(r"-\d{2,4}$", key):
        return [r for r in rows
                if re.sub(r"-\d{2,4}$", "", designation_key(r.get("designation", ""))) == key
                and not r.get("isChange")]
    return []


@dataclass
class NormacsCheckResult:
    kind: str
    ref: str
    query: str
    found: bool = False
    designation: str = ""
    name: str = ""
    status: str = ""
    status_hint: str = ""
    status_id: str = ""
    intro_date: str = ""
    status_date: str = ""
    doc_id: str = ""
    url: str = ""
    error: str = ""
    error_code: str = ""
    checked_at: str = ""
    status_history: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class NormacsClient:
    def __init__(self, base_url: str, *, login: str = "", password: str = "",
                 timeout_sec: float | None = None):
        self.base = base_url.rstrip("/") + "/"
        self.login_user = login.strip()
        self.login_password = password.strip()
        self.timeout = timeout_sec if timeout_sec is not None else normacs_timeout_sec()
        self.api = ""
        self._logged_in = False
        self.statuses = dict(DEFAULT_STATUSES)
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )
        self._lock = threading.RLock()

    def _json(self, url: str, *, data: dict | None = None, deadline: float | None = None):
        # Повторяем только чтение: повторный POST входа после потерянного ответа
        # может занимать ещё одну лицензию на сервере.
        attempts = 1 + (normacs_retries() if data is None else 0)
        last_error = None
        for attempt in range(attempts):
            try:
                return self._json_once(url, data=data, deadline=deadline)
            except NormacsError as exc:
                last_error = exc
                if not exc.retryable or attempt + 1 == attempts:
                    raise
                pause = min(1.0, 0.25 * (attempt + 1))
                if deadline is not None and deadline - time.monotonic() <= pause:
                    raise last_error
                log.info("NormaCS: повтор запроса %s после %s (%s/%s)",
                         self._stage(url), exc.code, attempt + 1, attempts - 1)
                time.sleep(pause)

    @staticmethod
    def _stage(url: str) -> str:
        path = urllib.parse.urlsplit(url).path
        if path.endswith("config.json"):
            return "настройки сервера"
        if path.endswith("auth.login"):
            return "вход"
        if path.endswith("documents"):
            return "поиск"
        if path.endswith("/info"):
            return "карточка документа"
        return "состояние API"

    def _json_once(self, url: str, *, data: dict | None = None, deadline: float | None = None):
        timeout = self.timeout
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise NormacsDeadline("Истёк бюджет проверки NormaCS")
            timeout = min(timeout, remaining)
        body = urllib.parse.urlencode(data).encode("utf-8") if data is not None else None
        req = urllib.request.Request(url, data=body, headers={
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Accept": "application/json", "User-Agent": "Belener/1.0",
        })
        try:
            with self._opener.open(req, timeout=timeout) as resp:
                value = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise NormacsError("NormaCS: вход отклонён или сессия истекла", code="auth") from None
            raise NormacsError(f"NormaCS: HTTP {exc.code} ({self._stage(url)})",
                               code="http", retryable=exc.code in (429, 500, 502, 503, 504)) from None
        except (urllib.error.URLError, OSError) as exc:
            reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
            target = urllib.parse.urlsplit(url)
            address = f"{target.hostname}:{target.port or (443 if target.scheme == 'https' else 80)}"
            if isinstance(reason, (TimeoutError, socket.timeout)):
                message, code = f"нет ответа от {address} за {timeout:.1f} с", "timeout"
            elif isinstance(reason, socket.gaierror):
                message, code = f"не удалось определить адрес {target.hostname} (DNS)", "dns"
            else:
                message, code = f"не удалось подключиться к {address}", "network"
            raise NormacsError(f"NormaCS: {message} ({self._stage(url)})",
                               code=code, retryable=True) from None
        except (json.JSONDecodeError, UnicodeError):
            raise NormacsError(f"NormaCS: сервер вернул некорректный JSON ({self._stage(url)})",
                               code="response") from None
        if isinstance(value, dict) and "errorCode" in value:
            raise NormacsError("NormaCS: сервер отклонил запрос")
        return value

    def _authenticated_json(self, url: str, *, deadline: float | None = None):
        try:
            return self._json(url, deadline=deadline)
        except NormacsError as exc:
            if exc.code != "auth":
                raise
            # Одна повторная авторизация на подтверждённое истечение сессии.
            self._logged_in = False
            self.login(deadline=deadline)
            return self._json(url, deadline=deadline)

    def healthy(self, *, deadline: float) -> bool:
        """Один короткий контрольный запрос отличает медленный поиск от сбоя API."""
        try:
            value = self._json_once(self.api + "documents.statuses", deadline=deadline)
            return isinstance(value, dict) and bool(value) and "errorCode" not in value
        except (NormacsError, NormacsDeadline):
            return False

    def discover_api(self, *, deadline: float | None = None) -> str:
        with self._lock:
            if self.api:
                return self.api
            config = self._json(self.base + "config.json", deadline=deadline)
            if not isinstance(config, dict):
                raise NormacsError("NormaCS: некорректный config.json")
            api = urllib.parse.urljoin(self.base, str(config.get("apiUrl") or ""))
            parsed, site = urllib.parse.urlsplit(api), urllib.parse.urlsplit(self.base)
            if (parsed.scheme not in ("http", "https") or parsed.hostname != site.hostname
                    or parsed.username or parsed.password or not config.get("apiUrl")
                    or (site.scheme == "https" and parsed.scheme != "https")):
                raise NormacsError("NormaCS: API должен находиться на том же сервере, что и веб-интерфейс")
            self.api = api.rstrip("/") + "/"
            return self.api

    def login(self, *, deadline: float | None = None) -> None:
        with self._lock:
            if self._logged_in:
                return
            if not self.login_user or not self.login_password:
                raise NormacsError("NormaCS: укажите логин и пароль в Интеграциях или .env")
            self.discover_api(deadline=deadline)
            user = self._json(self.api + "auth.login", data={
                "login": self.login_user, "password": self.login_password, "remember": 0,
            }, deadline=deadline)
            if not isinstance(user, dict) or "license" not in user:
                raise NormacsError("NormaCS: сервер не подтвердил вход")
            self._logged_in = True
            statuses = self._json(self.api + "documents.statuses", deadline=deadline)
            if isinstance(statuses, dict):
                self.statuses.update({str(k): str(v) for k, v in statuses.items()})

    def search(self, query: str, *, deadline: float | None = None) -> list[dict]:
        self.login(deadline=deadline)
        rows: list[dict] = []
        for offset in range(0, 500, 50):
            params = urllib.parse.urlencode({
                # prepareSearchQuery веб-интерфейса преобразует searchBy=designation
                # в API-параметр title (text означает поиск по содержимому).
                "title": query, "availableOnly": 0,
                # Полное количество совпадений не нужно: конец определяется
                # длиной страницы. Не запускаем лишний COUNT по всему фонду.
                "limit": 50, "offset": offset, "countTotal": 0,
            })
            data = self._authenticated_json(self.api + "documents?" + params, deadline=deadline)
            if not isinstance(data, dict) or not isinstance(data.get("items"), list):
                raise NormacsError("NormaCS: некорректный ответ поиска")
            page = data["items"]
            if any(not isinstance(r, dict) for r in page):
                raise NormacsError("NormaCS: некорректная строка поиска")
            rows.extend(page)
            if len(page) < 50:
                return rows
        raise NormacsError("NormaCS: более 500 совпадений; уточните обозначение")

    def card(self, doc_id: str, *, deadline: float | None = None) -> dict:
        self.login(deadline=deadline)
        card = self._authenticated_json(self.api + "documents/" + urllib.parse.quote(doc_id, safe="")
                                        + "/info?relations=off", deadline=deadline)
        if not isinstance(card, dict) or not card.get("id"):
            raise NormacsError("NormaCS: некорректная карточка документа")
        return card

    def close(self) -> None:
        if self._logged_in:
            try:
                self._json(self.api + "auth.logout", data={}, deadline=time.monotonic() + 3)
            except (NormacsError, NormacsDeadline):
                pass
            self._logged_in = False


def lookup_one(kind: str, ref: str, *, client: NormacsClient,
               deadline: float | None = None) -> NormacsCheckResult:
    query = normacs_query(kind, ref)
    out = NormacsCheckResult(kind=kind, ref=ref, query=query, checked_at=date.today().isoformat())
    try:
        matches = _matching_documents(query, client.search(query, deadline=deadline))
        if not matches:
            out.status = "Не найден в NormaCS"
            return out
        if len({designation_key(r.get("designation", "")) for r in matches}) > 1:
            out.status = "Несколько редакций — уточните год"
            return out
        row = matches[0]
        if not row.get("id"):
            raise NormacsError("NormaCS: в результате поиска отсутствует ID документа")
        card = client.card(str(row["id"]), deadline=deadline)
        if designation_key(card.get("designation", "")) != designation_key(row.get("designation", "")):
            raise NormacsError("NormaCS: обозначение карточки не совпадает с результатом поиска")
        out.found = True
        out.doc_id = str(card["id"])
        out.designation = str(card.get("designation") or "")
        out.name = str(card.get("title") or "")
        out.status_id = str(card["statusId"]) if card.get("statusId") is not None else ""
        out.status = client.statuses.get(out.status_id, "Неопределенный статус")
        out.status_hint = str(card.get("statusHint") or out.status)
        out.intro_date = str(card.get("startDate") or "")
        out.status_date = str(card.get("statusDate") or "")
        out.status_history = [str(x) for x in (card.get("statusHistory") or [])]
        out.url = client.base + "document/" + urllib.parse.quote(out.doc_id, safe="") + "/card"
    except NormacsDeadline:
        out.status = "Пропущено (бюджет времени)"
    except (NormacsError, TimeoutError) as exc:
        _set_failure(out, exc)
    return out


def _set_failure(out: NormacsCheckResult, exc: Exception) -> None:
    out.error_code = getattr(exc, "code", "timeout" if isinstance(exc, TimeoutError) else "error")
    out.status = {"timeout": "Таймаут NormaCS", "network": "Нет связи с NormaCS",
                  "dns": "Ошибка DNS NormaCS", "auth": "Ошибка входа NormaCS",
                  "unavailable": "Не проверен: NormaCS недоступен"}.get(out.error_code, "Ошибка проверки NormaCS")
    out.error = str(exc)


def check_normative_refs_normacs(refs: list[dict], *, client: NormacsClient | None = None,
                                 deadline: float | None = None,
                                 on_progress: Callable[[int, int], None] | None = None) -> list[NormacsCheckResult]:
    if not refs or not normacs_lookup_enabled():
        return []
    credentials = normacs_credentials()
    cli = client or NormacsClient(**credentials)
    checks: list[NormacsCheckResult] = []
    seen: set[str] = set()
    started = time.monotonic()
    try:
        # Если вход невозможен, не повторяем авторизацию для каждого норматива.
        login_error = ""
        login_exception = None
        login_expired = False
        consecutive_failures = 0
        outage = None
        try:
            cli.login(deadline=deadline)
        except NormacsDeadline:
            login_expired = True
        except NormacsError as exc:
            login_error = str(exc)
            login_exception = exc
        # Российские/межгосударственные нормативы первыми: медленные запросы
        # по СН/ТКП не должны расходовать всё окно до проверки ГОСТ и СНиП.
        ordered = sorted(refs, key=lambda item: 0 if str(item.get("kind") or "").upper()
                         in ("ГОСТ", "ГОСТ Р", "СНИП", "ISO", "EN") else 1)
        total = len({designation_key(normacs_query(str(item.get("kind") or ""), str(item.get("ref") or "")))
                     for item in refs if item.get("ref")})
        for item in ordered:
            kind, ref = str(item.get("kind") or "").strip(), str(item.get("ref") or "").strip()
            if not ref:
                continue
            query = normacs_query(kind, ref)
            key = designation_key(query)
            if key in seen:
                continue
            seen.add(key)
            if login_expired:
                check = NormacsCheckResult(kind, ref, query, status="Пропущено (бюджет времени)")
            elif login_error:
                check = NormacsCheckResult(kind, ref, query)
                _set_failure(check, login_exception)
            elif outage:
                check = NormacsCheckResult(kind, ref, query)
                _set_failure(check, outage)
            else:
                # Один документ не занимает timeout отдельно на каждой странице
                # поиска/карточке: ограничиваем всё обращение целиком.
                document_deadline = time.monotonic() + normacs_document_budget_sec()
                if deadline is not None:
                    document_deadline = min(document_deadline, deadline)
                check = lookup_one(kind, ref, client=cli, deadline=document_deadline)
                if check.status.startswith("Пропущено") and (deadline is None or time.monotonic() < deadline):
                    _set_failure(check, NormacsError("NormaCS: исчерпан таймаут проверки документа", code="timeout"))
                consecutive_failures = (consecutive_failures + 1
                                        if check.error_code in ("timeout", "network", "dns", "http") else 0)
                if consecutive_failures >= 3 and hasattr(cli, "healthy"):
                    probe_deadline = time.monotonic() + 5
                    if deadline is not None:
                        probe_deadline = min(probe_deadline, deadline)
                    if not cli.healthy(deadline=probe_deadline):
                        outage = NormacsError(
                            "NormaCS: API не отвечает на контрольный запрос после 3 сбоев подряд. "
                            "Проверка оставшихся документов не выполнена; проверьте доступ к API "
                            "и повторите проверку после восстановления связи.", code="unavailable")
                        log.warning("%s", outage)
                    consecutive_failures = 0
            checks.append(check)
            if check.error:
                if check.error_code != "unavailable" and (not login_error or len(checks) == 1):
                    log.warning("NormaCS lookup %s -> %s: %s", ref, check.status, check.error)
            elif not check.status.startswith("Пропущено"):
                log.info("NormaCS lookup %s -> %s (%s)", ref, check.status, check.designation)
            if on_progress:
                on_progress(len(checks), total)
        log.info("NormaCS batch: %s refs in %.1fs found=%s errors=%s skipped=%s", len(checks),
                 time.monotonic() - started, sum(c.found for c in checks), sum(bool(c.error) for c in checks),
                 sum(c.status.startswith("Пропущено") for c in checks))
        return checks
    finally:
        if client is None:
            cli.close()
