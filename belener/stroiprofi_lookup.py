"""Проверка актуальности ТНПА РБ через каталог СТРОЙПРОФИ.

Используется только API метаданных: обозначения, карточки и статусы на дату.
Текст загруженного пользователем документа на внешний сайт не передаётся.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime

from belener.config import stroiprofi_base_url, stroiprofi_credentials, stroiprofi_parallel_workers, stroiprofi_timeout_sec

log = logging.getLogger(__name__)
_HOST = "xn--h1abobedddj0a.xn--90ais"
_KINDS = ("ГОСТ", "ОСТ", "СТБ", "СТП", "СНиП", "СН", "ТКП", "СП", "ТР", "СНБ", "ТУ", "РД")
_SPACE = re.compile(r"\s+")
_AMENDMENT = re.compile(r"^(?:изменени[ея]|изм\.?|поправка)\s*№?\s*\d+\s*", re.I)
_PARENS = re.compile(r"\s*\([^)]*\)\s*$")
_EDITION = re.compile(r"(?:-(?P<dash>(?:19|20)\d{2}|\d{2})|\.(?P<dot>(?:19|20)\d{2}))$")


def _query(kind: str, ref: str) -> str:
    value = _SPACE.sub(" ", str(ref or "").strip())
    if not value:
        return ""
    label = str(kind or "").strip()
    if label and not value.casefold().startswith(label.casefold() + " "):
        value = f"{label} {value}"
    return value


def _key(value: str) -> str:
    value = _AMENDMENT.sub("", str(value or "").strip())
    value = _PARENS.sub("", value)
    value = value.upper().replace("–", "-").replace("—", "-").replace("−", "-")
    value = re.sub(r"\b(CH|СН)\s+(?=СН\b)", "", value)
    value = re.sub(r"\b(SP|СП)\s+(?=СП\b)", "", value)
    return re.sub(r"[^0-9A-ZА-ЯЁ]", "", value)


def _date(value: object) -> str:
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").strftime("%d.%m.%Y")
    except (TypeError, ValueError):
        return ""


def _iso_date(value: object) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _edition_family(value: str) -> tuple[str, str, int]:
    """Название для поиска семейства, строгий ключ номера и год редакции."""
    clean = _PARENS.sub("", _SPACE.sub(" ", str(value or "").strip()))
    match = _EDITION.search(clean)
    if not match:
        return clean, _key(clean), 0
    raw_year = match.group("dash") or match.group("dot")
    year = int(raw_year)
    if len(raw_year) == 2:
        year += 2000 if year <= 39 else 1900
    family = clean[:match.start()].rstrip(" .-")
    return family, _key(family), year


def _status(card: dict) -> str:
    raw = str(card.get("status") or "").strip().casefold()
    if any(word in raw for word in ("отмен", "утрат", "не действует")):
        return "отменён"
    if any(word in raw for word in ("не вступ", "не введ", "проект")):
        return "не введён"
    if "частич" in raw:
        return "частично действует"
    if raw in ("действующий", "действует", "актуален"):
        return "актуален"
    return "статус не определён"


@dataclass
class StroiprofiCheck:
    kind: str
    ref: str
    query: str
    found: bool = False
    stn_code: str = ""
    stn_name: str = ""
    intro_date: str = ""
    cancel_date: str = ""
    status: str = ""
    doc_id: str = ""
    error: str = ""
    url: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "kind": self.kind, "ref": self.ref, "query": self.query,
            "found": "1" if self.found else "0", "stn_code": self.stn_code,
            "stn_name": self.stn_name, "intro_date": self.intro_date,
            "cancel_date": self.cancel_date, "status": self.status,
            "doc_id": self.doc_id, "error": self.error, "url": self.url,
            "source": "stroiprofi",
        }


class StroiprofiError(RuntimeError):
    pass


class StroiprofiClient:
    def __init__(self, *, base_url: str | None = None, login: str | None = None,
                 password: str | None = None, timeout: int | None = None):
        credentials = stroiprofi_credentials()
        raw_url = (base_url or stroiprofi_base_url()).strip().rstrip("/")
        parsed = urllib.parse.urlsplit(raw_url)
        hostname = (parsed.hostname or "").encode("idna").decode("ascii")
        if parsed.scheme != "https" or hostname != _HOST or parsed.port is not None:
            raise StroiprofiError("Адрес Стройпрофи должен быть https://стройпрофи.бел")
        self.base_url = f"https://{_HOST}"
        self.login_name = credentials["login"] if login is None else login
        self.password = credentials["password"] if password is None else password
        self.timeout = timeout or stroiprofi_timeout_sec()
        self._token = ""
        self._login_lock = threading.Lock()

    def _request(self, path: str, *, body: dict | None = None, authenticated: bool = True) -> dict:
        headers = {"Accept": "application/json", "User-Agent": "AI-BELENER/1.0"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if authenticated and self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        request = urllib.request.Request(
            self.base_url + path,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None,
            headers=headers,
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                data = json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise StroiprofiError("неверные учётные данные или нет доступа к Стройпрофи") from exc
            raise StroiprofiError(f"Стройпрофи вернул HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise StroiprofiError(f"нет связи со Стройпрофи: {type(exc).__name__}") from exc
        if not isinstance(data, dict):
            raise StroiprofiError("Стройпрофи вернул неожиданный ответ")
        return data

    def ensure_login(self) -> None:
        # Метаданные фонда доступны и без входа; учётная запись расширяет доступ.
        if not self.login_name or not self.password or self._token:
            return
        with self._login_lock:
            if self._token:
                return
            data = self._request("/api/account/login", body={"login": self.login_name,
                                                                "password": self.password}, authenticated=False)
            token = data.get("token")
            if not isinstance(token, str) or not token:
                raise StroiprofiError("вход в Стройпрофи не подтверждён")
            self._token = token

    def search(self, query: str) -> list[dict]:
        params = urllib.parse.urlencode({"query": query, "limit": 30})
        data = self._request(f"/api/Tnpa/Check?{params}")
        items = data.get("items")
        if not isinstance(items, list):
            raise StroiprofiError("Стройпрофи вернул некорректный список документов")
        return [item for item in items if isinstance(item, dict)]

    def search_by_designation(self, query: str) -> list[dict]:
        """Основной каталог сайта: запасной поиск по полю «Обозначение»."""
        params = urllib.parse.urlencode({"pageNumber": 1, "pageSize": 100})
        data = self._request(
            f"/api/Tnpa/GetAll?{params}",
            body={"designation": query, "searchWord": "", "onlyTrByComplex": False},
        )
        items = data.get("items")
        if not isinstance(items, list):
            raise StroiprofiError("Стройпрофи вернул некорректный список документов")
        return [item for item in items if isinstance(item, dict)]

    def card(self, doc_id: int | str, *, as_of: date) -> dict:
        params = urllib.parse.urlencode({"id": str(doc_id), "asOf": as_of.isoformat()})
        return self._request(f"/api/Tnpa/?{params}")

    def lookup(self, kind: str, ref: str, *, as_of: date) -> StroiprofiCheck:
        query = _query(kind, ref)
        out = StroiprofiCheck(kind=kind, ref=ref, query=query)
        try:
            self.ensure_login()
            family_query, family_key, _ = _edition_family(query)
            is_amendment = bool(_AMENDMENT.match(query))
            candidates: dict[str, dict] = {}
            catalog_ids: set[str] = set()
            failures: list[StroiprofiError] = []

            def collect(search_query: str) -> None:
                for method, is_catalog in ((self.search, False), (self.search_by_designation, True)):
                    try:
                        rows = method(search_query)
                    except StroiprofiError as exc:
                        failures.append(exc)
                        continue
                    for row in rows:
                        doc_id = str(row.get("id") or "")
                        if doc_id.isdigit():
                            candidates[doc_id] = row
                            if is_catalog:
                                catalog_ids.add(doc_id)

            collect(family_query)

            def same_family(item: dict) -> bool:
                designation = str(item.get("designation") or "")
                if bool(_AMENDMENT.match(designation)) != is_amendment:
                    return False
                return _edition_family(designation)[1] == family_key

            matches = [item for item in candidates.values() if same_family(item)]
            if not matches and family_query != query:
                # Короткий запрос может не попасть в индекс Check; точное
                # обозначение остаётся резервным запросом.
                collect(query)
                matches = [item for item in candidates.values() if same_family(item)]
            if not matches:
                if not candidates and failures:
                    raise failures[0]
                prefix = _key(query)
                incomplete = len(re.sub(r"\D", "", query)) >= 4 and any(
                    _key(str(item.get("designation") or "")).startswith(prefix)
                    for item in candidates.values()
                )
                out.status = ("неполное обозначение — уточните номер и год" if incomplete
                              else "не найден в Стройпрофи")
                return out

            # Будущая редакция не подменяет ту, что действовала на выбранную дату.
            eligible = [item for item in matches
                        if not (start := _iso_date(item.get("activateDate") or item.get("date")))
                        or start <= as_of]
            ranked = eligible or matches

            def freshness(item: dict) -> tuple[int, date]:
                year = _edition_family(str(item.get("designation") or ""))[2]
                start = _iso_date(item.get("activateDate") or item.get("date")) or date.min
                return year, start

            newest = max(freshness(item) for item in ranked)
            top = [item for item in ranked if freshness(item) == newest]
            if len(top) > 1:
                # Одинаковый номер/год бывает у нескольких карточек. Если
                # основной каталог оставил одну, берём её; иначе не угадываем.
                catalog_top = [item for item in top if str(item.get("id")) in catalog_ids]
                if len(catalog_top) == 1:
                    top = catalog_top
            if len(top) > 1:
                out.status = "несколько совпадений — проверьте карточку"
                return out
            item = top[0]
            doc_id = item.get("id")
            if not isinstance(doc_id, (int, str)) or not str(doc_id).isdigit():
                raise StroiprofiError("Стройпрофи не вернул номер карточки")
            card = self.card(doc_id, as_of=as_of)
            if _edition_family(str(card.get("designation") or ""))[1] != family_key:
                raise StroiprofiError("номер карточки не совпадает с запросом")
            out.found = True
            out.stn_code = str(card.get("designation") or "")
            out.stn_name = str(card.get("name") or "")
            out.intro_date = _date(card.get("activateDate") or card.get("date"))
            out.cancel_date = _date(card.get("cancelDate"))
            out.status = _status(card)
            out.doc_id = str(doc_id)
            out.url = f"{self.base_url}/tnpa/document/{doc_id}"
            return out
        except StroiprofiError as exc:
            out.status = "ошибка проверки"
            out.error = str(exc)
            log.warning("Стройпрофи: не удалось проверить %s: %s", query, exc)
            return out


def check_normative_refs(refs: list[dict[str, str]], *, today: date | None = None,
                         deadline: float | None = None,
                         client: StroiprofiClient | None = None) -> tuple[list[dict[str, str]], list[StroiprofiCheck]]:
    source = list(refs or [])
    seen: set[tuple[str, str]] = set()
    items = []
    for ref in source:
        kind, label = str(ref.get("kind") or "").strip(), str(ref.get("ref") or "").strip()
        key = (kind.casefold(), _key(_query(kind, label)))
        if kind and label and key not in seen:
            seen.add(key)
            items.append((kind, label))
    if not items:
        return source, []
    service = client or StroiprofiClient()
    day = today or date.today()
    workers = min(stroiprofi_parallel_workers(), len(items))
    log.info("Стройпрофи: проверка %s обозначений (%s потоков)", len(items), workers)

    def check(item: tuple[str, str]) -> StroiprofiCheck:
        kind, ref = item
        if deadline is not None and time.monotonic() >= deadline:
            return StroiprofiCheck(kind, ref, _query(kind, ref), status="пропущено (бюджет времени)")
        return service.lookup(kind, ref, as_of=day)

    if workers <= 1:
        results = [check(item) for item in items]
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(check, item): i for i, item in enumerate(items)}
            results = [None] * len(items)
            for future in as_completed(futures):
                results[futures[future]] = future.result()
    log.info("Стройпрофи: проверка завершена, найдено %s/%s", sum(c.found for c in results), len(results))
    return source, results
