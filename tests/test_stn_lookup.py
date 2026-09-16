from datetime import date
from pathlib import Path

from belener.stn_lookup import (
    StnCheckResult,
    StnClient,
    StnLoginError,
    _digits_compatible,
    _iter_ocr_digit_variants,
    _is_network_unreachable,
    check_normative_refs_stn,
    is_stn_checkable,
    lookup_one,
    parse_card_html,
    parse_ru_date,
    refine_and_check_normative_refs,
    search_queries,
    search_query,
    stn_checks_to_markdown,
    validity_status,
)


def test_network_unreachable_detection():
    import urllib.error

    assert _is_network_unreachable(urllib.error.URLError(OSError(101, "Network is unreachable")))
    assert _is_network_unreachable("<urlopen error [Errno 113] No route to host>")
    assert not _is_network_unreachable("таймаут normy.stn.by")


def test_batch_retries_route_error_after_other_work(monkeypatch):
    monkeypatch.setattr("belener.stn_lookup.stn_lookup_enabled", lambda: True)
    monkeypatch.setattr("belener.stn_lookup.stn_parallel_workers", lambda: 1)

    class _Client:
        _logged_in = True
        _login_user = "user"
        _login_pass = "pass"

        def _ensure_logged_in(self):
            return None

    calls: dict[str, int] = {}

    def _fake_lookup(kind, ref, **_kwargs):
        calls[ref] = calls.get(ref, 0) + 1
        if ref == "СН 1.02.02" and calls[ref] == 1:
            return StnCheckResult(
                kind=kind,
                ref=ref,
                query=ref,
                found=False,
                status="ошибка проверки",
                error="<urlopen error [Errno 101] Network is unreachable>",
            )
        return StnCheckResult(
            kind=kind,
            ref=ref,
            query=ref,
            found=True,
            status="актуален",
            stn_code=ref,
        )

    monkeypatch.setattr("belener.stn_lookup.lookup_one", _fake_lookup)
    refs = [
        {"kind": "СН", "ref": "СН 1.02.02"},
        {"kind": "СП", "ref": "СП 1.02.01"},
    ]
    _, checks = refine_and_check_normative_refs(refs, client=_Client())

    assert calls == {"СН 1.02.02": 2, "СП 1.02.01": 1}
    assert all(check.found for check in checks)

CARD_HTML = """
<table class="doc-card-table">
<tr><td class="doc-card-header">Обозначение</td><td>СНиП 3.05.02-88</td></tr>
<tr><td class="doc-card-header">Дата введения</td><td>01.07.1988</td></tr>
<tr><td class="doc-card-header">Дата отмены</td><td>25.04.2026</td></tr>
</table>
"""

AMENDMENT_CARD_HTML = """
<table class="doc-card-table">
<tr><td class="doc-card-header">Обозначение</td><td>Изменение №1 СНиП 3.05.02-88</td></tr>
<tr><td class="doc-card-header">Дата введения</td><td>01.06.1994</td></tr>
<tr><td class="doc-card-header">Дата отмены</td><td>—</td></tr>
</table>
"""

SN_BASE_CARD_HTML = """
<table class="doc-card-table">
<tr><td class="doc-card-header">Обозначение</td><td>СН 2.01.01-2022</td></tr>
<tr><td class="doc-card-header">Дата введения</td><td>08.09.2022</td></tr>
<tr><td class="doc-card-header">Дата отмены</td><td>09.06.2025</td></tr>
</table>
"""

SN_AMENDMENT_CARD_HTML = """
<table class="doc-card-table">
<tr><td class="doc-card-header">Обозначение</td><td>Изменение №1 СН 2.01.01-2022</td></tr>
<tr><td class="doc-card-header">Дата введения</td><td>09.06.2025</td></tr>
<tr><td class="doc-card-header">Дата отмены</td><td>—</td></tr>
</table>
"""


def test_is_stn_checkable():
    assert is_stn_checkable("ГОСТ")
    assert is_stn_checkable("СНиП")
    assert is_stn_checkable("СН")
    assert is_stn_checkable("СП")
    assert is_stn_checkable("ОСТ")
    assert is_stn_checkable("СТП")
    assert not is_stn_checkable("ТУ")
    assert not is_stn_checkable("НРР")
    assert not is_stn_checkable("ПУЭ")


def test_sn_search_queries_include_sp_alias():
    qs = search_queries("СН", "СН 1.03.04-2020")
    assert qs[0] == "СН 1.03.04-2020"
    assert "СП 1.03.04-2020" in qs


def test_digit_compatible_blocks_1070_vs_10704():
    assert _digits_compatible("107091", "1070491") is False
    assert _digits_compatible("896275", "896975") is True


def test_ocr_digit_variants():
    variants = _iter_ocr_digit_variants("ГОСТ", "ГОСТ 8962-75")
    assert any("8969" in v for v in variants)


def test_search_query_strips_part_spec():
    assert search_query("ГОСТ", "В-20 ГОСТ 10705-80") == "ГОСТ 10705-80"
    assert search_query("ГОСТ", "В-Ст3пс ГОСТ 10705-80") == "ГОСТ 10705-80"


def test_search_query_strips_table_pos():
    assert search_query("ГОСТ", "13 ГОСТ 8969-75") == "ГОСТ 8969-75"


def test_tkp_normalizes_ocr_spaces():
    assert search_query("ТКП", "ТКП 45 - 4.03 - 267-2012") == "ТКП 45-4.03-267-2012"
    assert search_query("ТКП", "T K P 45-4.03-267-2012") == "ТКП 45-4.03-267-2012"
    assert search_query("ТКП", "ТКП45-4.03-267-2012 (02250)") == "ТКП 45-4.03-267-2012"
    qs = search_queries("ТКП", "Т К П 45 - 4.03 - 267 - 2012")
    assert qs[0] == "ТКП 45-4.03-267-2012"
    assert "45-4.03-267-2012" in qs


def test_tkp_queries_include_env_okp_code():
    qs = search_queries("ТКП", "ТКП 17.13-16-2014")
    assert "ТКП 17.13-16-2014 (02120)" in qs
    assert "17.13-16-2014 (02120)" in qs
    assert "ТКП 17.13-16-2014 (02250)" in qs


def test_stn_doctypes_cover_full_fund():
    from belener.stn_lookup import _STN_DOCTYPES

    # Не ограничиваемся стройкой: иначе ТКП других министерств «не находятся».
    assert _STN_DOCTYPES.get("doctype[19]") == "on"
    assert len(_STN_DOCTYPES) >= 30
    assert all(_STN_DOCTYPES[f"doctype[{i}]"] == "on" for i in (2, 4, 5, 6, 13, 14, 19, 23))


def test_ost_normalizes_space_before_dot():
    assert search_query("ОСТ", "ОСТ 34 10.761-97") == "ОСТ 34 10.761-97"


def test_parse_card_html_snip():
    fields = parse_card_html(CARD_HTML)
    assert fields["Обозначение"] == "СНиП 3.05.02-88"
    assert fields["Дата введения"] == "01.07.1988"
    assert fields["Дата отмены"] == "25.04.2026"


def test_validity_status_cancelled():
    assert (
        validity_status(
            parse_ru_date("01.07.1988"),
            parse_ru_date("25.04.2026"),
            today=date(2026, 6, 11),
        )
        == "отменён"
    )


def test_validity_status_active_before_cancel():
    assert (
        validity_status(
            parse_ru_date("01.07.1988"),
            parse_ru_date("25.04.2026"),
            today=date(2026, 1, 1),
        )
        == "актуален"
    )


class _FakeClient(StnClient):
    def __init__(self) -> None:
        self.base = "https://example.test/"
        self.timeout = 5
        self._logged_in = True
        self._login_user = ""
        self._login_pass = ""
        self._login_error = ""

    def search_all(self, queries: list[str]):
        for q in queries:
            if "3.05.02-88" in q:
                return [
                    {
                        "docid": "160",
                        "code": "СНиП 3.05.02-88",
                        "name": "Газоснабжение",
                        "activitydate": "1988-07-01",
                        "status": "1",
                    },
                    {
                        "docid": "117",
                        "code": "Изменение №1 СНиП 3.05.02-88",
                        "name": "Изменение №1 СНиП 3.05.02-88",
                        "activitydate": "1994-06-01",
                        "status": "1",
                    },
                ]
        return []

    def search_escalated(self, kind, ref, queries, *, max_queries=None, deadline=None):
        rows = self.search_all(queries[: max_queries or len(queries)])
        from belener.stn_lookup import _pick_best_match

        match = _pick_best_match(kind, ref, rows)
        return match, "; ".join(queries[:4])

    def search_escalated_rows(self, kind, ref, queries, *, max_queries=None, deadline=None):
        rows = self.search_all(queries[: max_queries or len(queries)])
        return rows, "; ".join(queries[:4])

    def fetch_card(self, doc_id: str) -> str:
        if doc_id == "117":
            return AMENDMENT_CARD_HTML
        assert doc_id == "160"
        return CARD_HTML

    def _ensure_logged_in(self) -> None:
        self._logged_in = True


def test_search_escalated_falls_back_to_full(monkeypatch):
    """Full search must run even when quick returns nothing."""

    class _QuickEmptyClient(StnClient):
        def __init__(self) -> None:
            self.base = "https://example.test/"
            self.timeout = 5
            self._logged_in = True
            self._login_user = ""
            self._login_pass = ""

        def search_quick_pages(self, query: str, *, max_pages: int = 1):
            return []

        def search_full(self, query: str):
            if "2073-2010" in query:
                return [
                    {
                        "docid": "42",
                        "code": "СТБ 2073-2010",
                        "name": "Бетоны",
                        "activitydate": "2010-01-01",
                        "status": "1",
                    }
                ]
            return []

        def search_escalated_rows(self, kind, ref, queries, *, max_queries=None, deadline=None):
            rows = []
            for q in queries[: max_queries or len(queries)]:
                rows.extend(self.search_full(q))
            return rows, "; ".join(queries[:4])

        def fetch_card(self, doc_id: str) -> str:
            return """
            <tr><td class="doc-card-header">Обозначение</td><td>СТБ 2073-2010</td></tr>
            <tr><td class="doc-card-header">Дата введения</td><td>01.01.2010</td></tr>
            <tr><td class="doc-card-header">Дата отмены</td><td>—</td></tr>
            """

        def _ensure_logged_in(self) -> None:
            self._logged_in = True

    monkeypatch.setattr("belener.stn_lookup.stn_lookup_enabled", lambda: True)
    monkeypatch.setattr("belener.stn_lookup.stn_ocr_variant_limit", lambda: 0)
    res = lookup_one("СТБ", "СТБ 2073-2010", client=_QuickEmptyClient(), today=date(2026, 7, 6))
    assert res.found
    assert res.stn_code == "СТБ 2073-2010"


def test_prefer_freshest_amendment_when_base_canceled(monkeypatch):
    """База отменена — берём актуальное изменение с более поздней датой введения."""
    monkeypatch.setattr("belener.stn_lookup.stn_lookup_enabled", lambda: True)
    res = lookup_one("СНиП", "СНиП 3.05.02-88", client=_FakeClient(), today=date(2026, 6, 11))
    assert res.found
    assert "Изменение" in res.stn_code
    assert res.intro_date == "01.06.1994"
    assert res.status == "актуален"


def test_authenticated_empty_search_falls_back_to_open_fund(monkeypatch):
    monkeypatch.setattr("belener.stn_lookup.stn_ocr_variant_limit", lambda: 0)

    class _OpenFund(_FakeClient):
        def __init__(self) -> None:
            super().__init__()
            self._logged_in = False
            self.card_requested = False

        def search_all(self, queries):
            if any("1.02.02" in q for q in queries):
                return [
                    {
                        "docid": "open-1",
                        "code": "СН 1.02.02-2023",
                        "name": "Состав проектной документации",
                        "activitydate": "2023-01-01",
                        "status": "1",
                    }
                ]
            return []

        def fetch_card(self, doc_id: str) -> str:
            assert doc_id == "open-1"
            self.card_requested = True
            return """
            <tr><td class="doc-card-header">Обозначение</td><td>СН 1.02.02-2023</td></tr>
            <tr><td class="doc-card-header">Дата введения</td><td>01.01.2023</td></tr>
            <tr><td class="doc-card-header">Дата отмены</td><td>—</td></tr>
            """

    class _EmptyIps(_FakeClient):
        def __init__(self, fund) -> None:
            super().__init__()
            self._allow_open_fallback = True
            self.fund = fund

        def search_all(self, queries):
            return []

        def _open_fund_client(self):
            return self.fund

        def fetch_card(self, doc_id: str) -> str:
            raise AssertionError("IPS card endpoint must not be used for an open-fund row")

    fund = _OpenFund()
    result = lookup_one("СН", "СН 1.02.02", client=_EmptyIps(fund), today=date(2026, 9, 16))

    assert result.found
    assert result.stn_code == "СН 1.02.02-2023"
    assert result.status == "актуален"
    assert fund.card_requested


def test_prefer_sn_amendment_2025_over_canceled_base(monkeypatch):
    """СН 2.01.01: база 2022 отменена, Изменение №1 действует с 09.06.2025."""
    monkeypatch.setattr("belener.stn_lookup.stn_lookup_enabled", lambda: True)

    class _SnClient(_FakeClient):
        def search_all(self, queries: list[str]):
            for q in queries:
                if "2.01.01" in q.replace(" ", ""):
                    return [
                        {
                            "docid": "b1",
                            "code": "СН 2.01.01-2022",
                            "name": "Основы проектирования",
                            "activitydate": "2022-09-08",
                            "status": "0",
                        },
                        {
                            "docid": "a1",
                            "code": "Изменение №1 СН 2.01.01-2022",
                            "name": "Основы проектирования",
                            "activitydate": "2025-06-09",
                            "status": "1",
                        },
                    ]
            return []

        def fetch_card(self, doc_id: str) -> str:
            if doc_id == "a1":
                return SN_AMENDMENT_CARD_HTML
            if doc_id == "b1":
                return SN_BASE_CARD_HTML
            raise AssertionError(doc_id)

    res = lookup_one("СН", "СН 2.01.01", client=_SnClient(), today=date(2026, 9, 10))
    assert res.found
    assert res.stn_code == "Изменение №1 СН 2.01.01-2022"
    assert res.intro_date == "09.06.2025"
    assert res.status == "актуален"
    assert not (res.cancel_date or "").strip() or res.cancel_date in ("—", "-")


def test_check_fund_kinds(monkeypatch):
    monkeypatch.setattr("belener.stn_lookup.stn_lookup_enabled", lambda: True)
    refs = [
        {"kind": "ОСТ", "ref": "ОСТ 34 10.761-97"},
        {"kind": "ГОСТ", "ref": "ГОСТ 10705-80"},
        {"kind": "СНиП", "ref": "СНиП 3.05.02-88"},
        {"kind": "СТП", "ref": "СТП 34 17.101"},
    ]
    out = check_normative_refs_stn(refs, client=_FakeClient(), today=date(2026, 6, 11))
    assert len(out) == 4
    assert {c.kind for c in out} == {"ГОСТ", "ОСТ", "СНиП", "СТП"}


def test_tkp_search_queries_prioritize_02250():
    qs = search_queries("ТКП", "ТКП 45-3.02-7-2005")
    assert qs[0] == "ТКП 45-3.02-7-2005"
    assert "02250" in qs[1]
    assert len(qs) >= 4


def test_tkp_spaced_number_search():
    assert search_query("ТКП", "ТКП 45 - 3.02 - 7 - 2005") == "ТКП 45-3.02-7-2005"


def test_stn_table_shows_not_found():
    checks = [
        StnCheckResult("ГОСТ", "ГОСТ 10704-91", "", found=True, intro_date="01.01.1993", status="актуален"),
        StnCheckResult("ТКП", "ТКП 45-3.02-7-2005", "", found=False, status="нет в ИПС"),
    ]
    md = "\n".join(stn_checks_to_markdown(checks, check_date=date(2026, 6, 19)))
    assert "10704-91" in md
    assert "45-3.02-7-2005" in md
    assert "нет в ИПС" in md
    assert "Проверено на листе: 2" in md


def test_stb_ocr_leading_zero_to_1097():
    from belener.normative_refs import format_stb_number

    assert format_stb_number("097-2012") == "1097-2012"


def test_parse_card_html_ips():
    ips_html = Path(__file__).resolve().parents[1].joinpath(
        "scripts", "_ips_card_sample.html"
    )
    if not ips_html.is_file():
        return
    fields = parse_card_html(ips_html.read_text(encoding="utf-8"))
    assert "ТКП 45-4.03-267-2012" in fields["Обозначение"]
    assert fields["Дата введения"] == "01.12.2012"
    assert fields["Дата отмены"] == "21.09.2020"


def test_bad_login_reports_ips_error_not_silent_not_found(monkeypatch):
    c = StnClient(login="bad-user", password="bad-pass")
    monkeypatch.setattr(c, "_verify_ips_session", lambda: False)
    try:
        c.login("bad-user", "bad-pass")
        assert False, "expected StnLoginError"
    except StnLoginError as e:
        assert "IPS" in str(e)
    assert not c._logged_in
    res = lookup_one("ГОСТ", "ГОСТ 27772-2015", client=c, today=date(2026, 7, 8))
    assert not res.found
    assert "IPS" in res.status
    assert res.status != "нет в ИПС"


def test_missing_credentials_show_login_hint(monkeypatch):
    monkeypatch.setattr("belener.stn_lookup.stn_lookup_enabled", lambda: True)

    class _NoCredsClient(StnClient):
        def __init__(self) -> None:
            super().__init__(login="", password="")

    refs = [{"kind": "ГОСТ", "ref": "ГОСТ 27772-2015"}]
    _, checks = refine_and_check_normative_refs(refs, client=_NoCredsClient(), today=date(2026, 7, 8))
    assert len(checks) == 1
    assert "PDF_STN_LOGIN" in checks[0].status


def test_tkp_not_found_without_ips(monkeypatch):
    monkeypatch.setattr("belener.stn_lookup.stn_lookup_enabled", lambda: True)

    class _EmptyClient(_FakeClient):
        _logged_in = False

        def search_all(self, queries):
            return []

        def search_escalated(self, kind, ref, queries, *, max_queries=None, deadline=None):
            return None, "; ".join(queries[:4])

        def search_escalated_rows(self, kind, ref, queries, *, max_queries=None, deadline=None):
            return [], "; ".join(queries[:4])

        def _ensure_logged_in(self) -> None:
            self._logged_in = False

    res = lookup_one("ТКП", "ТКП 45-4.03-267-2012", client=_EmptyClient(), today=date(2026, 6, 11))
    assert not res.found
    assert "IPS" in res.status


def test_ref_keeps_sheet_ref_on_stn(monkeypatch):
    monkeypatch.setattr("belener.stn_lookup.stn_lookup_enabled", lambda: True)

    class _VariantClient(_FakeClient):
        def search_all(self, queries):
            for q in queries:
                if "8969" in q:
                    return [
                        {
                            "docid": "999",
                            "code": "ГОСТ 8969-75",
                            "name": "Электроды",
                            "activitydate": "1975-01-01",
                            "status": "1",
                        }
                    ]
            return super().search_all(queries)

        def search_escalated(self, kind, ref, queries, *, max_queries=None, deadline=None):
            rows = self.search_all(queries[: max_queries or len(queries)])
            from belener.stn_lookup import _pick_best_match

            match = _pick_best_match(kind, ref, rows)
            return match, "; ".join(queries[:4])

        def search_escalated_rows(self, kind, ref, queries, *, max_queries=None, deadline=None):
            rows = self.search_all(queries[: max_queries or len(queries)])
            return rows, "; ".join(queries[:4])

        def fetch_card(self, doc_id: str) -> str:
            if doc_id == "999":
                return (
                    '<table class="doc-card-table">'
                    "<tr><td class=\"doc-card-header\">Обозначение</td><td>ГОСТ 8969-75</td></tr>"
                    "<tr><td class=\"doc-card-header\">Дата введения</td><td>01.01.1975</td></tr>"
                    "</table>"
                )
            return super().fetch_card(doc_id)

    refs = [{"kind": "ГОСТ", "ref": "ГОСТ 8962-75"}]
    refined, checks = refine_and_check_normative_refs(refs, client=_VariantClient(), today=date(2026, 6, 11))
    assert refined[0]["ref"] == "ГОСТ 8962-75"
    assert not checks[0].found

    md = "\n".join(
        stn_checks_to_markdown(
            [
                StnCheckResult(
                    kind="СНиП",
                    ref="СНиП 3.05.02-88",
                    query="СНиП 3.05.02-88",
                    found=True,
                    stn_code="СНиП 3.05.02-88",
                    intro_date="01.07.1988",
                    cancel_date="25.04.2026",
                    status="отменён",
                )
            ]
        )
    )
    assert "normy.stn.by" in md
    assert "01.07.1988" in md
    assert "отменён" in md
