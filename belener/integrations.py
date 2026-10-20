"""Registry of external integrations configurable in admin."""

from __future__ import annotations

from typing import Any

INTEGRATIONS: list[dict[str, Any]] = [
    {
        "id": "stroiprofi",
        "name": "Стройпрофи (стройпрофи.бел)",
        "description": "Проверка статуса нормативов РБ на выбранную дату.",
        "prefix": "stroiprofi",
        "fields": [
            {
                "key": "base_url",
                "label": "Адрес портала",
                "secret": False,
                "placeholder": "https://стройпрофи.бел",
            },
            {
                "key": "login",
                "label": "Логин Стройпрофи",
                "secret": False,
                "placeholder": "user@company.by",
            },
            {
                "key": "password",
                "label": "Пароль Стройпрофи",
                "secret": True,
                "placeholder": "••••••••",
            },
        ],
    },
    {
        "id": "tnpa",
        "name": "ТНПА (tnpa.by)",
        "description": "Проверка актуальности нормативов в Национальном фонде ТНПА.",
        "prefix": "tnpa",
        "fields": [
            {
                "key": "base_url",
                "label": "Адрес портала",
                "secret": False,
                "placeholder": "https://tnpa.by",
            },
        ],
    },
]


def integration_by_id(integration_id: str) -> dict[str, Any] | None:
    for item in INTEGRATIONS:
        if item["id"] == integration_id:
            return item
    return None


def settings_key(prefix: str, field_key: str) -> str:
    return f"{prefix}.{field_key}"
