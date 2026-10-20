"""Итоговая индикация двух независимых реестров, без подмены их статусов."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RegistryVerdict:
    state: str
    label: str
    tone: str
    note: str = ""
    conflict: bool = False


def stn_state(*, enabled: bool, found: bool, status: str = "", error: str = "") -> str:
    if not enabled:
        return "disabled"
    value = (status or "").strip().casefold()
    if error or "ошибка" in value or "пропущ" in value or "нет связи" in value:
        return "error"
    if found:
        if value == "актуален":
            return "active"
        if value in ("отменён", "отменен", "не действует"):
            return "inactive"
        if value in ("не введён", "не введен"):
            return "upcoming"
        if "частично" in value:
            return "partial"
        return "unknown"
    if value.startswith(("нет в", "не в", "не найден")):
        return "missing"
    return "unknown"


def normacs_state(*, enabled: bool, found: bool, status_id: str = "",
                  status: str = "", error: str = "") -> str:
    if not enabled:
        return "disabled"
    value = (status or "").strip().casefold()
    if error or "ошибка" in value or "таймаут" in value or "нет связи" in value or "пропущ" in value:
        return "error"
    if found:
        return {"1": "active", "0": "inactive", "2": "partial", "4": "upcoming"}.get(str(status_id), "unknown")
    if value.startswith("не найден"):
        return "missing"
    if "несколько редакций" in value:
        return "error"
    return "unknown"


def combined_verdict(stn: str, normacs: str, *, rb_label: str = "Стройдок · РБ") -> RegistryVerdict:
    """Зелёный = действует хотя бы в одном; явное расхождение всегда видно."""
    sources = ((rb_label, stn), ("NormaCS · РФ", normacs))
    active = [name for name, state in sources if state == "active"]
    inactive = [name for name, state in sources if state == "inactive"]
    partial = [name for name, state in sources if state == "partial"]
    upcoming = [name for name, state in sources if state == "upcoming"]
    incomplete = any(state in ("error", "unknown") for _, state in sources)
    if active:
        conflict = bool(inactive or partial or upcoming)
        label = "Действует в обоих реестрах" if len(active) == 2 else f"Действует · {active[0]}"
        note = "Статусы реестров расходятся; проверьте условия применения." if conflict else ""
        if incomplete and len(active) == 1:
            note = (note + " " if note else "") + "Второй реестр не проверен."
        return RegistryVerdict("active", label, "good", note, conflict)
    if partial:
        partial_label = ", ".join(partial)
        return RegistryVerdict("partial", f"Частично действует · {partial_label}", "warn",
                               "Проверьте ограничения в карточке источника.", bool(inactive or upcoming))
    if upcoming:
        return RegistryVerdict("upcoming", "Не введён / проект", "warn",
                               "Документ ещё не действует в указанном реестре.", bool(inactive))
    if inactive and incomplete:
        return RegistryVerdict("unknown", "Проверка неполная", "warn",
                               f"{', '.join(inactive)}: не действует; второй реестр не проверен.")
    if inactive:
        label = "Не действует в обоих реестрах" if len(inactive) == 2 else f"Не действует · {inactive[0]}"
        return RegistryVerdict("inactive", label, "bad")
    if incomplete:
        return RegistryVerdict("unknown", "Статус не подтверждён", "neutral",
                               "Не удалось достоверно проверить оба реестра.")
    return RegistryVerdict("missing", "Не найден в реестрах", "neutral",
                           "Отсутствие в поиске не означает отмену документа.")
