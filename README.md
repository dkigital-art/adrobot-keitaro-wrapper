# AdRobot — обёртка над Keitaro Admin API

Тестовое задание **«AdRobot»**: веб-приложение для создания кампаний в Keitaro и управления потоками с офферами.

**Стек:** FastAPI, SQLAlchemy, Alembic; фронтенд — HTML + vanilla JS + CSS (без сборки).

---

## Быстрый старт

### 1. API-ключ на стенде

1. Откройте https://keitaro-for-tests.etolegko.com/ и войдите через GitHub.
2. Нажмите **«API ключ»** вверху и скопируйте токен.

- Base URL API: `https://keitaro-for-tests.etolegko.com/admin_api/v1`
- На стенде уже есть: домен `1813`, группа `FORTESTS` (`10332`), источник `FORTESTS` (`836`).

### 2. Установка и запуск

```bash
cd adrobot
python -m venv .venv

# Windows
.venv\Scripts\activate
# Linux / macOS
# source .venv/bin/activate

pip install -r requirements.txt

# Windows
copy .env.example .env
# Linux / macOS
# cp .env.example .env
```

В `.env` укажите `KEITARO_API_KEY` (остальное уже заполнено под стенд).

```bash
alembic upgrade head
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Откройте http://127.0.0.1:8000/  
Swagger: `/docs` · проверка состояния: `/api/health`

Путь SQLite и файл `.env` считаются от каталога `adrobot/`, а не от текущей папки запуска.

### 3. Тесты

```bash
pytest
```

Тесты офлайн: SQLite in-memory + заглушка Keitaro (`tests/fakes.py`). Покрыты доли с `pin`, FETCH / PUSH / CANCEL / REMOVE / BRING BACK, удаление кампании, коды `400` / `409` / `410` / `502` и запись в SyncLog.

---

## Функциональность

### Создатель кампаний

Форма: **Name**, **Geo** (коды стран через запятую), **Offer** (автокомплит по каталогу Keitaro).

В Keitaro создаётся кампания с доменом / группой / источником из `.env` и двумя потоками:

1. geo-фильтр → редирект на `https://google.com`;
2. поток с выбранным оффером (`share: 100`).

Если создание потоков упало — недособранная кампания удаляется в Keitaro, локально ничего не остаётся.

### Список кампаний

- **FETCH FROM KT** — импортирует кампании, созданные напрямую в Keitaro, обновляет имена известных и убирает из локального списка те, которых уже нет в KT.
- **Удалить** (крестик) — после подтверждения удаляет кампанию в Keitaro и локально. Если в KT её уже нет — чистит только локальную копию.

### Редактор

В UI показываются только потоки с офферами. Geo-поток хранится локально, но не отображается.

| Действие | Поведение |
|----------|-----------|
| **FETCH STREAMS FROM KT** | Синхронизирует потоки и офферы из Keitaro. Пропавший оффер → `removed` (серый, можно **BRING BACK**), из БД не стирается. Поток, удалённый в KT, удаляется и локально. При draft → `409` (перезапись с `force=true`). Если удалена вся кампания → `410` и предложение удалить локальную копию. |
| Черновик (**draft**) | Локальные изменения подсвечиваются жёлтым и не уходят в Keitaro до PUSH. |
| **PUSH TO KT** | Отправляет активные офферы в Keitaro и сохраняет `published_snapshot`. Если поток удалён в KT, а кампания жива — поток создаётся заново. Если удалена кампания → `410`. |
| **CANCEL** | Откат к последнему опубликованному snapshot. |
| **REMOVE** / **BRING BACK** | Soft-delete (`share=0`, `removed`) или возврат в active с пересчётом долей. |
| **pin** / доля | Закреплённые % не трогаются; остаток до 100% делится между unpinned active (минимум **1%** на active). Ручная доля у незапиненного оффера. Невалидное состояние → `400`. |
| **Удалить** | То же, что крестик в списке: Keitaro + локально, с предупреждением. |

### Diff, журнал, ошибки

- У draft-потока блок **«Было в KT → Станет после PUSH»** (добавления, удаления, смена доли и pin).
- **SyncLog** — `create_campaign` / `fetch` / `push` / `cancel`, успех и ошибки; `GET /api/campaigns/{id}/logs` и сворачиваемый блок внизу страницы кампании.
- Сетевые сбои и ошибки Keitaro → **502** с понятным текстом. Keep-alive к Keitaro и кэш каталога офферов (5 мин, прогрев при старте).

---

## Структура проекта

```
adrobot/
  app/
    main.py              # FastAPI, статика, alembic upgrade при старте
    config.py            # настройки из .env
    db.py, models.py, schemas.py
    api/                 # campaigns, offers, streams, errors
    services/            # Keitaro-клиент, создание, sync, веса, diff, SyncLog
  alembic/               # миграции
  static/                # HTML / JS / CSS
  tests/                 # pytest + FakeKeitaro
  .env.example
  requirements.txt
  pytest.ini
```

---

## Частые проблемы

| Проблема | Что проверить |
|----------|----------------|
| `KEITARO_API_KEY не задан` | Файл `.env` в каталоге `adrobot/`, ключ без кавычек |
| `502` при запросах к Keitaro | Ключ, вход через GitHub на стенде, доступность `KEITARO_URL` |
| `409` при FETCH | В потоке есть draft: PUSH, CANCEL или подтвердите перезапись (`force=true`) |
| `410` — кампания удалена в KT | Удалите локальную копию или создайте кампанию заново |
| UI выглядит по-старому | Обновите страницу (`Ctrl+F5`); ассеты с `?v=N` |

---

Учебный проект для тестового задания.
