# БелнипиAI — проверка ГОСТ на чертеже

Локальное веб-приложение: загрузите PDF или скан листа — система найдёт **ГОСТ, ОСТ, СТП, ТУ, СТБ** и проверит актуальность на [normy.stn.by](https://normy.stn.by).

Проверка выполняется только через **Стройдок (`normy.stn.by`)**; обращения к `tnpa.by` в веб-пайплайне отключены.

Чертежи **не отправляются в облако**: OCR выполняется в контейнере (Tesseract). На STN уходят только **коды** нормативных документов.

## Требования

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (Windows / macOS) или Docker Engine + Compose v2 (Linux)
- 8 ГБ RAM минимум (рекомендуется 12 ГБ)
- Свободный порт **8090**

## Быстрый старт (клон → Docker → тест)

### Windows (PowerShell)

```powershell
git clone https://github.com/yelizavetafitil/AI_BELENER.git
cd AI_BELENER
Copy-Item .env.example .env
# опционально: учётная запись ИПС для проверки актуальности на normy.stn.by
# notepad .env
.\scripts\up_gost_stack.ps1
```

Скрипт создаёт `G:\BelenerCache` (кэш OCR-тайлов на SSD) и поднимает стек. Если диска `G:` нет — используйте только `docker-compose.fast.yml` (см. Linux ниже).

### Linux / macOS

```bash
git clone https://github.com/yelizavetafitil/AI_BELENER.git
cd AI_BELENER
cp .env.example .env
chmod +x scripts/up_gost_stack.sh
./scripts/up_gost_stack.sh
```

### Проверка

1. Откройте **http://localhost:8090**
2. Прикрепите PDF чертежа
3. Нажмите «Все ГОСТ на листе» или отправьте без текста (запрос подставится автоматически)

```bash
# smoke: настройки и тесты внутри контейнера
docker compose exec web python scripts/check_normative_setup.py
docker compose exec web python -m pytest tests/ -q

# bench на PDF из папки scan/ (положите файл локально, папка не в git)
docker compose exec web python scripts/bench_normative_pdfs.py /app/scan/your_drawing.pdf
```

**STN без логина:** список нормативов с листа работает; таблица актуальности на normy.stn.by — при настроенном Стройдоке в [админке](#админ-панель-admin) или при `PDF_STN_LOGIN` / `PDF_STN_PASSWORD` в `.env`.

## Админ-панель (`/admin`)

Отдельный служебный интерфейс для IT/администраторов. **На главной странице приложения ссылки на админку нет** — URL передают только ответственным людям.

| URL | Назначение |
|-----|------------|
| http://localhost:8090/ | Основное приложение (проверка ГОСТ) |
| http://localhost:8090/admin/ | Настройка организации |

Админка и приложение — **один процесс** (`app.py`), **одна база** PostgreSQL. Настройки из админки сразу читает основной код:

```
/admin  →  PostgreSQL (tenant_settings, tenant_integrations)
                ↓
belener/config.py  →  stn_login(), stn_password(), stn_base_url()
                ↓
belener/stn_lookup.py  →  проверка актуальности на normy.stn.by
```

| Что настраивается в админке | Где используется |
|-----------------------------|------------------|
| **Интеграции → Стройдок** (логин/пароль, URL) | Проверка актуальности ГОСТ/СП в основном приложении |
| **Active Directory** | Вход только в `/admin` (список администраторов панели) |
| Параметры OCR, бюджеты, модели | По-прежнему в `.env` |

**Приоритет учётных данных Стройдok:** запись в админке (БД) → legacy в БД → переменные `PDF_STN_*` в `.env`.

### Первый запуск админки

1. Поднимите стек (см. [Быстрый старт](#быстрый-старт-клон--docker--тест)).
2. Откройте **http://localhost:8090/admin/** — мастер первоначальной настройки.
3. Шаг **Active Directory:** LDAP URI, Base DN, служебная учётка, список администраторов панели.
4. Войдите под своей учётной записью AD.
5. Вкладка **Интеграции** → добавьте **Стройдок** (normy.stn.by) с логином и паролем ИПС → **Проверить** → **Сохранить**.
6. На главной странице (http://localhost:8090) загрузите PDF — актуальность будет проверяться с учётными данными из админки.

Пересборка после изменений кода:

```bash
docker compose up -d --build web
```

Сброс только настроек AD (мастер настройки снова откроется):

```bash
docker compose exec db psql -U belener -d belnipiai -c "DELETE FROM tenant_settings WHERE key LIKE 'ad.%';"
```

## Настройка `.env`

| Переменная | Назначение |
|------------|------------|
| `WEB_PORT` | Порт веб-интерфейса (по умолчанию 8090) |
| `PDF_STN_LOGIN` / `PDF_STN_PASSWORD` | Учётная запись ИПС (запасной вариант, если не задано в админке) |
| `PDF_STN_LOOKUP` | `1` — проверять на STN, `0` — только список с листа |
| `PDF_TILE_OCR_DPI` | Качество OCR (320 — баланс скорость/точность) |
| `PDF_GOST_CHECK_BUDGET` | Базовый лимит OCR (сек.) на 1 лист (по умолчанию 600) |
| `PDF_GOST_EXTRA_PER_PAGE_SEC` | Добавка с/лист для коротких томов; далее ступени 85%/72% (не ниже 22/20 с) |
| `PDF_GOST_CHECK_BUDGET_MAX` | Потолок OCR (по умолчанию 14400 ≈ 4 ч; тома 200+ листов) |
| `PDF_STN_BATCH_BUDGET` | Минимальное отдельное окно Стройдока после завершения OCR |
| `PDF_TILE_OCR_MAX_PAGES` | `0` — все листы; иначе лимит листов |
| `BELENER_SSD_ROOT` | Путь к кэшу на SSD (по умолчанию `G:/BelenerCache`) |
| `PDF_ZONE_CACHE` | `1` — кэш PNG-тайлов на SSD между запусками |

После изменения `.env`:

```powershell
.\scripts\up_gost_stack.ps1 -Recreate -NoBuild
```

## Полезные команды

```bash
# Логи
docker compose logs -f web

# Остановить
docker compose down

# Остановить и удалить базу (все чаты)
docker compose down -v

# Очистить только чаты, не трогая контейнеры
docker compose exec db psql -U belener -d belnipiai -c "TRUNCATE messages, conversations CASCADE;"

# Проверка настроек
docker compose exec web python scripts/check_normative_setup.py

# Тесты
docker compose exec web python -m pytest tests/test_normative_refs.py tests/test_stn_lookup.py -q
```

## Что внутри

| Путь | Описание |
|------|----------|
| `app.py` | Веб-сервер и API (приложение + `/admin`) |
| `belener/` | OCR, извлечение ГОСТ, проверка STN, настройки в БД |
| `admin/`, `static/admin.js` | Админ-панель (отдельный URL, без ссылки с главной) |
| `static/`, `index.html` | Интерфейс проверки ГОСТ |
| `docker-compose.yml` | Базовый стек (web + PostgreSQL) |
| `docker-compose.fast.yml` | Профиль «Проверка ГОСТ на листе» |
| `data/training/` | Данные для дообучения моделей (для ГОСТ **не нужны**) |
| `docs/` | Расширенные сценарии (Surya, DeepSeek, полный разбор чертежа) |

## Приватность

- `PDF_LOCAL_ONLY=1` — без облачных LLM и vision
- `PDF_REPORT_LLM=0` — отчёт только из OCR и парсера
- На normy.stn.by передаются коды документов (ГОСТ 10704-91 и т.п.), не изображения чертежа
- **Номера ГОСТ не подставляются** из «ожидаемого списка»: в отчёт попадает только то, что прочитано с листа; спорные OCR-варианты разрешаются голосованием по тайлам, без таблицы замен

## Расширенные режимы

Полный разбор чертежа, GPU-OCR и другие профили — см. каталог `docs/` и файлы `docker-compose.*.yml`, `.env.*.example`.
