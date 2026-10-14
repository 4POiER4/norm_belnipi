"""Проверка подключения и одного известного документа без вывода учётных данных."""
import sys
import argparse
import socket
import time
from urllib.parse import urlsplit
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from belener.config import normacs_credentials
from belener.normacs_lookup import NormacsClient, lookup_one


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--diagnose", action="store_true", help="Проверить веб-порт и API до авторизации")
    args = parser.parse_args()
    # Подключаем сохранённые настройки интеграций при проверке внутри Docker.
    import app
    from belener.integration_store import bind_db_getter as bind_integrations
    from belener.settings_store import bind_db_getter

    bind_db_getter(app.get_db)
    bind_integrations(app.get_db)
    client = NormacsClient(**normacs_credentials())
    try:
        if args.diagnose:
            api = client.discover_api(deadline=time.monotonic() + 10)
            for name, url in (("Веб-интерфейс", client.base), ("API", api)):
                parsed = urlsplit(url)
                port = parsed.port or (443 if parsed.scheme == "https" else 80)
                try:
                    with socket.create_connection((parsed.hostname, port), timeout=4):
                        print(f"{name}: {parsed.hostname}:{port} — соединение установлено", flush=True)
                except OSError:
                    print(f"{name}: {parsed.hostname}:{port} — соединение не установлено", flush=True)
                    if name == "API":
                        print("Восстановите доступ к API: открывающаяся веб-страница не подтверждает работу проверки.")
                        return 1
        result = lookup_one("ГОСТ", "ГОСТ 21105-87", client=client)
        print("NormaCS:", result.designation or result.ref)
        print("Статус:", result.status)
        if result.error:
            print("Ошибка:", result.error)
        return 0 if result.found else 1
    except Exception as exc:
        from belener.normacs_lookup import NormacsError, NormacsDeadline
        if not isinstance(exc, (NormacsError, NormacsDeadline)):
            raise
        print("NormaCS:", str(exc))
        return 1
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
