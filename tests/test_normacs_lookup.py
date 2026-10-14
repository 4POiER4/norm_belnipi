import json
import time
from io import BytesIO
from urllib.parse import parse_qs, urlsplit

import pytest

from belener import normacs_lookup as nc


class FakeClient:
    base = "http://example.test:9999/normacsweb/"
    statuses = nc.DEFAULT_STATUSES

    def __init__(self, rows=None, card=None):
        self.rows = rows or []
        self.info = card or {}
        self.login_calls = 0
        self.card_calls = []

    def login(self, **kwargs):
        self.login_calls += 1

    def search(self, query, **kwargs):
        return self.rows

    def card(self, doc_id, **kwargs):
        self.card_calls.append(doc_id)
        return self.info


@pytest.mark.parametrize("designation,status_id,status", [
    ("ГОСТ 21105-87", 2, "Частично действует"),
    ("ГОСТ 21105-87", 0, "Не действует"),
    ("ГОСТ 21105-87", 1, "Действует"),
    ("ГОСТ 21105-87", 4, "Проект"),
    ("ГОСТ 21105-87", None, "Неопределенный статус"),
])
def test_card_status_not_collapsed(designation, status_id, status):
    card = {"id": "1.761", "designation": designation, "statusId": status_id,
            "startDate": "1988-01-01", "statusHistory": ["Заменён; применим для ТР"]}
    client = FakeClient([card], card)
    check = nc.lookup_one("ГОСТ", designation, client=client)
    assert check.found and check.status == status
    assert check.intro_date == "1988-01-01"
    assert check.status_history == card["statusHistory"]
    assert check.url.endswith("/document/1.761/card")
    assert check.to_dict()["found"] is True


def test_gost_r_is_not_gost():
    client = FakeClient([{"id": "wrong", "designation": "ГОСТ Р 21.101-2020"}])
    check = nc.lookup_one("ГОСТ", "ГОСТ 21.101-2020", client=client)
    assert not check.found and check.status == "Не найден в NormaCS"
    assert not client.card_calls


def test_short_year_and_long_year_equivalent():
    card = {"id": "1.3uv", "designation": "ГОСТ 21.101-97", "statusId": 0}
    check = nc.lookup_one("ГОСТ", "ГОСТ 21.101-1997", client=FakeClient([card], card))
    assert check.found and check.status == "Не действует"


def test_yearless_multiple_revisions_not_arbitrarily_selected():
    client = FakeClient([{"id": "1", "designation": "СП 1.02-2020"},
                         {"id": "2", "designation": "СП 1.02-2024"}])
    check = nc.lookup_one("СП", "СП 1.02", client=client)
    assert not check.found and "уточните год" in check.status
    assert not client.card_calls


def test_amendments_and_wrong_card_not_used():
    row = {"id": "1", "designation": "ГОСТ 21105-87", "isChange": True}
    assert not nc.lookup_one("ГОСТ", row["designation"], client=FakeClient([row])).found
    row["isChange"] = False
    check = nc.lookup_one("ГОСТ", row["designation"], client=FakeClient([row], {
        "id": "2", "designation": "ГОСТ 21105-2020"}))
    assert not check.found and check.error


def test_login_error_not_reported_as_absence_or_retried_225_times(monkeypatch):
    monkeypatch.setattr(nc, "normacs_lookup_enabled", lambda: True)
    monkeypatch.setattr(nc, "normacs_credentials", lambda: {})
    client = FakeClient()
    def fail(**kwargs):
        client.login_calls += 1
        raise nc.NormacsError("Вход отклонён")
    client.login = fail
    refs = [{"kind": "ГОСТ", "ref": f"ГОСТ {i}-2020"} for i in range(225)]
    checks = nc.check_normative_refs_normacs(refs, client=client)
    assert client.login_calls == 1 and len(checks) == 225
    assert all(c.status == "Ошибка проверки NormaCS" and c.error for c in checks)


def test_batch_deduplicates_canonical_refs(monkeypatch):
    monkeypatch.setattr(nc, "normacs_lookup_enabled", lambda: True)
    monkeypatch.setattr(nc, "normacs_credentials", lambda: {})
    refs = [{"kind": "ГОСТ", "ref": ref} for ref in ["ГОСТ 21.101-1997", "ГОСТ21.101-97"]]
    assert len(nc.check_normative_refs_normacs(refs, client=FakeClient())) == 1


def test_expired_deadline_and_empty_batch(monkeypatch):
    monkeypatch.setattr(nc, "normacs_lookup_enabled", lambda: True)
    monkeypatch.setattr(nc, "normacs_credentials", lambda: {})
    client = nc.NormacsClient("http://example.test/", login="test", password="secret")
    assert nc.check_normative_refs_normacs([], client=client) == []
    checks = nc.check_normative_refs_normacs([{"kind": "ГОСТ", "ref": "ГОСТ 1-2000"}],
                                            client=client, deadline=time.monotonic() - 1)
    assert checks[0].status == "Пропущено (бюджет времени)"
    assert not checks[0].error


def test_api_discovery_login_and_search_pagination():
    client = nc.NormacsClient("http://example.test:9999/normacsweb/", login="test", password="secret")
    calls = []
    class Opener:
        def open(self, req, timeout):
            calls.append((req, timeout))
            path = urlsplit(req.full_url).path
            if path.endswith("config.json"):
                result = {"apiUrl": "http://example.test:8888/normacsweb/api"}
            elif path.endswith("auth.login"):
                assert parse_qs(req.data.decode())["login"] == ["test"]
                result = {"license": {}}
            elif path.endswith("documents.statuses"):
                result = {"2": "Частично действует"}
            elif path.endswith("auth.logout"):
                result = {}
            else:
                params = parse_qs(urlsplit(req.full_url).query)
                assert params["title"] == ["ГОСТ 1"]
                assert params["availableOnly"] == ["0"]
                assert params["countTotal"] == ["0"]
                assert "text" not in params and "searchBy" not in params
                offset = int(params["offset"][0])
                result = {"items": [{"id": str(i)} for i in range(50)] if offset == 0 else []}
            return BytesIO(json.dumps(result).encode())
    client._opener = Opener()
    assert len(client.search("ГОСТ 1")) == 50
    assert all("statusId=" not in req.full_url for req, _ in calls)
    client.close()
    assert not client._logged_in
    assert len([req for req, _ in calls if req.full_url.endswith("auth.login")]) == 1


def test_do_not_send_credentials_to_other_api_host():
    client = nc.NormacsClient("http://example.test/", login="test", password="secret")
    client._json = lambda *args, **kwargs: {"apiUrl": "http://other.test/api"}
    with pytest.raises(nc.NormacsError, match="том же сервере"):
        client.login()


def test_credentials_priority_and_enable_toggle(monkeypatch):
    from belener import config, integration_store as store
    monkeypatch.setenv("PDF_NORMACS_LOGIN", "env-user")
    monkeypatch.setenv("PDF_NORMACS_PASSWORD", "env-secret")
    monkeypatch.setenv("PDF_NORMACS_LOOKUP", "auto")
    monkeypatch.setattr(store, "list_sites", lambda: [{"id": "1", "kind": "normacs"}])
    monkeypatch.setattr(store, "_get_site_secrets", lambda _: {
        "base_url": "http://example.test/", "login": "db-user", "password": "db-secret"})
    assert config.normacs_credentials()["login"] == "db-user"
    assert config.normacs_lookup_enabled()
    monkeypatch.setenv("PDF_NORMACS_LOOKUP", "0")
    assert not config.normacs_lookup_enabled()
    monkeypatch.setattr(store, "list_sites", lambda: [])
    assert config.normacs_credentials()["login"] == "env-user"


def test_report_separates_sources_escapes_details_and_handles_serialized_checks():
    from belener.normative_extract import normative_refs_to_markdown
    from bs4 import BeautifulSoup
    check = nc.NormacsCheckResult("ГОСТ", "ГОСТ21.101-97", "ГОСТ21.101-97", found=True,
        designation="ГОСТ 21.101-97", status="Частично действует", status_id="2",
        status_hint='<script>unsafe</script>', url="http://example.test/document/1/card")
    html = normative_refs_to_markdown([{"kind": "ГОСТ", "ref": "ГОСТ 21.101-1997"}],
                                     include_tnpa=False, normacs_checks=[check.to_dict()])
    soup = BeautifulSoup(html, "html.parser")
    assert len(soup.select("thead th")) == len(soup.select("tbody td")) == 6
    assert "Статус NormaCS (РФ)" in soup.get_text()
    assert soup.select_one(".normacs-status-2").text == "Частично действует"
    assert len(soup.select("a.normacs-link")) == 1 and not soup.select("script")
    check.found = False
    html = normative_refs_to_markdown([{"kind": "ГОСТ", "ref": check.ref}],
                                     include_tnpa=False, normacs_checks=[check.to_dict()])
    assert "Найдено в NormaCS: 0" in html


def test_normacs_only_report_has_no_stn_columns_or_warning(monkeypatch):
    from belener import config
    from belener.normative_extract import normative_refs_to_markdown
    from bs4 import BeautifulSoup

    monkeypatch.delenv("PDF_STN_LOOKUP", raising=False)
    assert not config.stn_lookup_enabled()
    check = nc.NormacsCheckResult(
        "ГОСТ", "ГОСТ 21.101-1997", "ГОСТ 21.101-1997",
        found=True, status="Действует", status_id="1",
        url="http://example.test/document/1/card",
    )
    html = normative_refs_to_markdown(
        [{"kind": "ГОСТ", "ref": check.ref}], include_stn=False,
        include_tnpa=False, include_normacs=True,
        normacs_checks=[check.to_dict()], stn_error="Старая ошибка Стройдока",
    )
    soup = BeautifulSoup(html, "html.parser")
    assert [cell.text for cell in soup.select("thead th")] == [
        "Обозначение", "NormaCS", "Статус NormaCS (РФ)",
    ]
    assert len(soup.select("tbody td")) == 3
    assert soup.select_one("tbody tr.row-active") is not None
    assert "найдено в NormaCS: 1" in html
    assert "действует: 1" in html
    assert "Стройдок" not in html


def test_normacs_only_error_is_not_marked_as_cancellation():
    from belener.normative_extract import normative_refs_to_markdown
    from bs4 import BeautifulSoup

    check = nc.NormacsCheckResult(
        "ГОСТ", "ГОСТ 21.101-1997", "ГОСТ 21.101-1997",
        status="Ошибка проверки NormaCS", error="Нет связи",
    )
    html = normative_refs_to_markdown(
        [{"kind": "ГОСТ", "ref": check.ref}], include_stn=False,
        include_tnpa=False, normacs_checks=[check.to_dict()],
    )
    row = BeautifulSoup(html, "html.parser").select_one("tbody tr")
    assert "row-canceled" not in row.get("class", [])
    assert "Нет связи" in row.text


def test_post_ocr_worker_new_deadline_independent_of_stn(monkeypatch):
    import app
    from belener import config
    monkeypatch.setattr(config, "normacs_lookup_enabled", lambda: True)
    def check(refs, **kwargs):
        assert kwargs["deadline"] > time.monotonic() + 100
        return [nc.NormacsCheckResult("ГОСТ", "ГОСТ 1-2000", "ГОСТ 1-2000", status="Действует")]
    monkeypatch.setattr(nc, "check_normative_refs_normacs", check)
    result = {"normative_refs": [{"kind": "ГОСТ", "ref": "ГОСТ 1-2000"}], "page_count": 225}
    assert app._start_normacs_checks(result, requested=True).wait(3)
    assert result["normacs_enabled"] and len(result["normacs_checks"]) == 1


def test_post_ocr_worker_error_always_finishes(monkeypatch):
    import app
    from belener import config
    monkeypatch.setattr(config, "normacs_lookup_enabled", lambda: True)
    def fail(*args, **kwargs):
        raise nc.NormacsError("Server error")
    monkeypatch.setattr(nc, "check_normative_refs_normacs", fail)
    result = {"normative_refs": []}
    assert app._start_normacs_checks(result, requested=True).wait(3)
    assert "не удалось" in result["normacs_error"]


def normacs_pdf_payload():
    return {
        "title": "Проверка нормативных документов", "filename": "normacs-test.pdf",
        "headers": ["Обозначение", "Стройдок", "Введен (Стройдок)",
                    "Отменен (Стройдок)", "NormaCS", "Статус NormaCS (РФ)"],
        "rows": [{"cells": [{"text": "ГОСТ 21105-87"}, {"text": "—"},
                             {"text": "—"}, {"text": "—"},
                             {"text": "NormaCS", "href": "http://example.test/document/1/card"},
                             {"text": "Частично действует\nВведен: 1988-01-01"}]}],
    }


def test_pdf_export_retains_normacs_column_and_link():
    import fitz
    from belener.normative_pdf import build_normative_pdf_bytes
    doc = fitz.open(stream=build_normative_pdf_bytes(normacs_pdf_payload()), filetype="pdf")
    text = "".join(page.get_text() for page in doc)
    assert "Частично действует" in text
    assert "Статус NormaCS (РФ)" in text
    assert "1988-01-01" in text and "текущие статусы базы РФ" in text
    assert any(link.get("uri") == "http://example.test/document/1/card"
               for page in doc for link in page.get_links())


def test_batch_prioritizes_gost_limits_whole_document_and_reports_progress(monkeypatch):
    monkeypatch.setattr(nc, "normacs_lookup_enabled", lambda: True)
    monkeypatch.setattr(nc, "normacs_credentials", lambda: {})
    monkeypatch.setattr(nc, "normacs_timeout_sec", lambda: 15)
    monkeypatch.setattr(nc, "normacs_document_budget_sec", lambda: 32)
    calls, progress = [], []
    client = FakeClient()
    def search(query, **kwargs):
        calls.append(query)
        assert 0 < kwargs["deadline"] - time.monotonic() <= 32
        return []
    client.search = search
    refs = [{"kind": "СН", "ref": "СН 1.02.02"}, {"kind": "ГОСТ", "ref": "ГОСТ 21105-87"}]
    checks = nc.check_normative_refs_normacs(refs, client=client,
        deadline=time.monotonic()+100, on_progress=lambda done,total:progress.append((done,total)))
    assert calls == ["ГОСТ 21105-87", "СН 1.02.02"]
    assert len(checks) == 2 and progress == [(1,2), (2,2)]


def test_per_document_timeout_not_reported_as_exhausted_batch(monkeypatch):
    monkeypatch.setattr(nc, "normacs_lookup_enabled", lambda: True)
    monkeypatch.setattr(nc, "normacs_credentials", lambda: {})
    monkeypatch.setattr(nc, "lookup_one", lambda kind,ref,**kwargs:
        nc.NormacsCheckResult(kind,ref,ref,status="Пропущено (бюджет времени)"))
    checks = nc.check_normative_refs_normacs([{"kind":"СН","ref":"СН 1.02.02"}],
                                            client=FakeClient(),deadline=time.monotonic()+100)
    assert checks[0].status == "Таймаут NormaCS"
    assert "таймаут" in checks[0].error


def test_large_batch_budget_accounts_for_each_document(monkeypatch):
    from belener.config import normacs_batch_budget_sec
    monkeypatch.setenv("PDF_NORMACS_TIMEOUT", "15")
    monkeypatch.setenv("PDF_NORMACS_RETRIES", "1")
    monkeypatch.setenv("PDF_NORMACS_BUDGET_MAX", "10000")
    assert normacs_batch_budget_sec(184) == 5918
    monkeypatch.setenv("PDF_NORMACS_BUDGET_MAX", "3600")
    assert normacs_batch_budget_sec(184) == 3600
    assert normacs_batch_budget_sec(1000) == 3600


def test_temporary_read_failure_retried_but_login_post_not_replayed(monkeypatch):
    import urllib.error
    monkeypatch.setattr(nc, "normacs_retries", lambda: 1)
    monkeypatch.setattr(nc.time, "sleep", lambda _: None)
    client = nc.NormacsClient("http://example.test:9999/", timeout_sec=15)
    calls = []
    class Opener:
        def open(self, req, timeout):
            calls.append(req)
            if len(calls) == 1 or req.data is not None:
                raise urllib.error.URLError(TimeoutError("timed out"))
            return BytesIO(b'{"items":[]}')
    client._opener = Opener()
    assert client._json("http://example.test:8888/api/documents", deadline=time.monotonic()+32) == {"items": []}
    assert len(calls) == 2
    with pytest.raises(nc.NormacsError, match="example.test:8888") as caught:
        client._json("http://example.test:8888/api/auth.login", data={"password":"do-not-log"})
    assert caught.value.code == "timeout" and len(calls) == 3
    assert "do-not-log" not in str(caught.value)


def test_non_transient_http_and_invalid_json_not_retried(monkeypatch):
    import urllib.error
    monkeypatch.setattr(nc, "normacs_retries", lambda: 2)
    client = nc.NormacsClient("http://example.test/")
    class Opener:
        calls = 0
        def open(self, req, timeout):
            self.calls += 1
            raise urllib.error.HTTPError(req.full_url,404,"Not Found",{},None)
    opener = Opener(); client._opener = opener
    with pytest.raises(nc.NormacsError, match="HTTP 404"):
        client._json("http://example.test/api/documents")
    assert opener.calls == 1
    opener.open = lambda *args,**kwargs: BytesIO(b'<!DOCTYPE html>')
    with pytest.raises(nc.NormacsError) as caught:
        client._json("http://example.test/api/documents")
    assert caught.value.code == "response"


def test_retry_respects_deadline(monkeypatch):
    client = nc.NormacsClient("http://example.test/")
    calls = []
    def fail(*args,**kwargs):
        calls.append(1)
        raise nc.NormacsError("timeout",code="timeout",retryable=True)
    monkeypatch.setattr(client,"_json_once",fail)
    monkeypatch.setattr(nc.time,"sleep",lambda _:pytest.fail("must not sleep past deadline"))
    with pytest.raises(nc.NormacsError):
        client._json("http://example.test/api/documents",deadline=time.monotonic()+0.01)
    assert len(calls) == 1


def test_expired_session_reauthenticated_only_once():
    client = nc.NormacsClient("http://example.test/",login="test",password="secret")
    client._logged_in = True
    reads, logins = [], []
    def read(*args,**kwargs):
        reads.append(1)
        if len(reads) == 1: raise nc.NormacsError("expired",code="auth")
        return {"items": []}
    def login(**kwargs):
        logins.append(1); client._logged_in = True
    client._json = read; client.login = login
    assert client.search("ГОСТ 1") == []
    assert len(reads) == 2 and len(logins) == 2  # initial guard and one re-login


def test_outage_stops_only_after_failed_control_probe(monkeypatch):
    monkeypatch.setattr(nc,"normacs_lookup_enabled",lambda:True)
    monkeypatch.setattr(nc,"normacs_credentials",lambda:{})
    client=FakeClient(); calls=[]; progress=[]; probes=[]
    def search(query,**kwargs):
        calls.append(query)
        raise nc.NormacsError("no response",code="timeout",retryable=True)
    client.search=search
    client.healthy=lambda **kwargs: probes.append(kwargs) or False
    refs=[{"kind":"СП","ref":f"СП {i}.13330.2020"} for i in range(10)]
    checks=nc.check_normative_refs_normacs(refs,client=client,
        on_progress=lambda a,b:progress.append((a,b)))
    assert len(calls)==3 and len(probes)==1 and len(checks)==10
    assert [c.error_code for c in checks[:3]]==["timeout"]*3
    assert all(c.status=="Не проверен: NormaCS недоступен" for c in checks[3:])
    assert progress[-1]==(10,10)


def test_slow_queries_do_not_disable_healthy_api(monkeypatch):
    monkeypatch.setattr(nc,"normacs_lookup_enabled",lambda:True)
    monkeypatch.setattr(nc,"normacs_credentials",lambda:{})
    client=FakeClient(); calls=[]
    def search(query,**kwargs):
        calls.append(query)
        if len(calls)<=3:raise nc.NormacsError("slow search",code="timeout")
        return []
    client.search=search; client.healthy=lambda **kwargs:True
    checks=nc.check_normative_refs_normacs([{"kind":"СП","ref":f"СП {i}"} for i in range(5)],client=client)
    assert len(calls)==5 and checks[-1].status=="Не найден в NormaCS"


def test_failure_reason_visible_escaped_and_summary_not_absence():
    from belener.normative_extract import normative_refs_to_markdown
    from bs4 import BeautifulSoup
    check=nc.NormacsCheckResult("СП","СП 22.13330.2016","СП 22.13330.2016",
        status="Таймаут NormaCS",error='API:8888 <script>unsafe</script>',error_code="timeout")
    html=normative_refs_to_markdown([{"kind":"СП","ref":check.ref}],include_tnpa=False,
                                   normacs_checks=[check.to_dict()])
    soup=BeautifulSoup(html,"html.parser")
    assert soup.select_one('.normacs-error-detail').text==check.error
    assert not soup.select('script')
    assert 'не удалось проверить 1' in soup.select_one('.normacs-warning').text
    assert 'не означает отсутствие или отмену' in soup.get_text()


def test_normacs_not_started_when_explicitly_disabled(monkeypatch):
    import app
    from belener import config
    monkeypatch.setattr(config,'normacs_lookup_enabled',lambda:True)
    monkeypatch.setattr(nc,'check_normative_refs_normacs',lambda *a,**k:pytest.fail('Must not query RF by default'))
    result={'normative_refs':[{'kind':'ГОСТ','ref':'ГОСТ 1-2020'}]}
    assert app._start_normacs_checks(result, requested=False).is_set()
    assert not result['normacs_enabled'] and 'normacs_checks' not in result


def test_requested_but_disabled_normacs_reports_configuration_problem(monkeypatch):
    import app
    from belener import config
    monkeypatch.setattr(config,'normacs_lookup_enabled',lambda:False)
    result={}
    assert app._start_normacs_checks(result,requested=True).is_set()
    assert 'не настроена' in result['normacs_error'] and not result['normacs_enabled']


def test_default_normacs_timeout_increased_to_30_seconds(monkeypatch):
    from belener.config import normacs_timeout_sec,normacs_document_budget_sec
    monkeypatch.delenv('PDF_NORMACS_TIMEOUT',raising=False)
    monkeypatch.setenv('PDF_NORMACS_RETRIES','1')
    assert normacs_timeout_sec()==30 and normacs_document_budget_sec()==62
    monkeypatch.setenv('PDF_NORMACS_TIMEOUT','invalid')
    assert normacs_timeout_sec()==30
