# Локальный запуск в VS Code (Windows)

Схема: Postgres и Redis крутятся в Docker, сам бот запускается из VS Code — так работают
брейкпоинты и видно, что происходит внутри.

> **Код ни разу не запускался.** Первый прогон, скорее всего, что-то вскроет. Это нормально,
> так и задумано — локальный запуск нужен именно чтобы это увидеть.

---

## 1. Поставить настоящий Python

Сейчас в системе `python` — это заглушка из Microsoft Store: она печатает `Python`,
ничего не выполняет и возвращает код 49. Пока она перехватывает команду, ничего не заработает.

**Поставить Python 3.12** с [python.org](https://www.python.org/downloads/) — при установке
обязательно отметить галочку **«Add python.exe to PATH»**.

Затем отключить перехват: **Параметры → Приложения → Дополнительные параметры приложений →
Псевдонимы выполнения приложения** — выключить оба переключателя `python.exe` и `python3.exe`.

Проверка в новом окне терминала:

```powershell
python --version
```

Должно вывести `Python 3.12.x`. Если по-прежнему просто `Python` — псевдонимы не отключились
или не перезапущен терминал.

## 2. Виртуальное окружение и зависимости

В корне проекта:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Если PowerShell ругается на политику выполнения:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

Установка займёт пару минут — тянется `instagrapi` со своими зависимостями.

## 3. Поднять базу и Redis

```powershell
docker compose -f docker-compose.dev.yml up -d
```

Это отдельный compose-файл только под разработку: в отличие от боевого, оба сервиса
проброшены на `localhost`, иначе Python с хоста до них не достучится.

Проверить, что поднялись:

```powershell
docker compose -f docker-compose.dev.yml ps
```

## 4. Заполнить .env

```powershell
copy .env.example .env
```

Открыть `.env` и заменить адреса на локальные — **это главное отличие от сервера**:

```
DATABASE_URL=postgresql+asyncpg://giga:devpassword@localhost:5432/giga_farm
REDIS_URL=redis://localhost:6379/0
```

`devpassword` — пароль по умолчанию из `docker-compose.dev.yml`.

Сгенерировать ключ шифрования (окружение уже активировано):

```powershell
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Результат вписать в `VAULT_ENCRYPTION_KEY`.

Минимум, чтобы бот стартовал — эти пять, без любой из них падает при импорте `config.py`:

| Переменная | Откуда |
|---|---|
| `TELEGRAM_BOT_TOKEN` | @BotFather → `/newbot` |
| `ANTHROPIC_API_KEY` | console.anthropic.com → API Keys |
| `DATABASE_URL` | строка выше |
| `VAULT_ENCRYPTION_KEY` | сгенерирован выше |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | путь к JSON; если Drive пока нет — см. ниже |

**Если Google Drive ещё не настроен**, а запустить хочется: `config.py` требует эту
переменную при импорте, но не проверяет, что файл существует. Можно временно указать любой
путь — бот стартует, а при создании аккаунта честно скажет, что папки создать не удалось.

## 5. Запустить

Открыть папку проекта в VS Code. Поставить расширения из рекомендованных (VS Code предложит
сам) — **Python** и **Python Debugger**.

Выбрать интерпретатор: `Ctrl+Shift+P` → **Python: Select Interpreter** → тот, что в `.venv`.

Дальше **F5** — в панели запуска есть две конфигурации:

- **Бот (polling)** — основной запуск
- **Healthcheck (проверка ключей)** — что подхватилось, что нет

Обе подтягивают `.env` автоматически.

## 6. Проверить, что живое

Напиши боту в Telegram:

```
создать блогершу
```

Пойдёт интервью из 15 вопросов, в конце соберётся карточка персонажа. Потом `bio` или
`сценарии` — проверишь генерацию вариантов.

Брейкпоинты имеет смысл ставить в `services/claude_service.py::generate_variants` —
там видно, какой промпт ушёл и что вернулось.

## Что может пойти не так

| Симптом | Причина |
|---|---|
| `python` печатает `Python` и выходит | Не отключены псевдонимы из Microsoft Store, шаг 1 |
| `KeyError: 'TELEGRAM_BOT_TOKEN'` | Не заполнена переменная в `.env` |
| `ConnectionRefusedError` на 5432 | Не поднят `docker-compose.dev.yml` |
| Ошибка авторизации в базе | Пароль в `DATABASE_URL` не совпал с `POSTGRES_PASSWORD` |
| `TypeError` про `output_config` | Старая версия `anthropic` — нужна 1.1.0, переставь зависимости |
| Ошибки Google при создании аккаунта | Drive не настроен — ожидаемо, аккаунт всё равно создастся |

## Остановить базу

```powershell
docker compose -f docker-compose.dev.yml down
```

Данные сохранятся в томе. Чтобы стереть базу полностью — добавить `-v`.
