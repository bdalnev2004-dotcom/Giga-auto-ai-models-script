# Giga Automation — Telegram bot skeleton

Рабочий каркас Telegram-бота из мастер-документа "Giga Automation": единый бэкенд,
триггер-словарь, FSM-диалоги с уточняющими вопросами, аппрувы по номеру/эмодзи,
хранение в Google Drive по нумерованной структуре, зашифрованный волт для кредов,
ежедневные напоминания/публикация сторис.

**Объём публикации (уточнено): автоматический постинг — только Instagram Reels
через instagrapi.** Фото/карусели/сторис можно генерировать и утверждать как контент
(остаются в Drive), но автопостинг у них не запускается. Других площадок в проекте
нет — код TikTok/YouTube/VK удалён, а не отключён.

## Структура

```
config.py              # настройки + шаблон папок Drive + целевые количества контента
triggers.py             # словарь триггеров -> сценарий (doc §2)
fsm/states.py           # состояния диалогов
handlers/
  account.py             # /account N — переключение контекста
  scenarios.py            # генерик-раннер диалогов + банк уточняющих вопросов (doc §3)
  approvals.py            # номер/эмодзи -> аппрув/отбраковка (doc §4, §8)
  scheduler.py            # 12:00/15:00 напоминания, 20:00 публикация сторис (doc §5)
services/
  claude_service.py       # оркестрация, генерация копирайта
  drive_service.py         # создание папок, загрузка/листинг файлов
  vault.py                 # шифрование кредов (Fernet)
  instagram_service.py     # Instagram: рилс/фото/сторис со ссылкой (instagrapi)
  higgsfield_service.py    # фото/видео/лого
  elevenlabs_service.py    # озвучка
  vyra_service.py          # монтаж (через MCP)
db/
  models.py                # Account, AccountPlatform, Credential, ContentItem, AuditLogEntry
  session.py                # async SQLAlchemy сессия
bot.py                    # entrypoint
```

## Запуск

- **На сервере** — [DEPLOY.md](DEPLOY.md), всё в Docker одной командой.
- **Локально в VS Code** — [RUN_LOCAL.md](RUN_LOCAL.md), с отладчиком и брейкпоинтами.

## Что уже работает, а что — заглушки

**Есть логика:**
- триггер-словарь и распознавание команд
- переключение аккаунта `/account N` + запись `ChatBinding` в БД
- генерик FSM-диалог с уточняющими вопросами (данные реально собираются и уходят в Claude)
- `create_brand`/`create_blogger` — создают `Account`, сеют `AccountPlatform(instagram)`,
  вызывают `drive_service.create_account_folder_tree`, привязывают чат
- аппрув по номеру/списку номеров/эмодзи, включая отбраковку пачки
- рабочий цикл перегенерации: `GenerationJob` хранит scenario_id + answers +
  revision_notes, при ❌ генерация запускается заново с замечаниями
- `PUBLISH_ROUTING` — таблица «content_type → сервис публикации»
- создание нумерованной структуры папок в Drive
- шифрование кредов (Fernet)
- модель БД целиком под доменную модель из документа
- крон-джобы на 12:00/15:00/20:00 по реальным `(account_id, chat_id)` из БД
- Redis для FSM в проде (fallback на MemoryStorage для dev)

**Заглушки (`raise NotImplementedError` или `TODO`)** — реальные вызовы внешних API,
т.к. нужны боевые ключи и точные контракты эндпоинтов.

*Level 1 — блокирует первый рабочий цикл (Reels → Instagram):*
- Vyra: `assemble_reel` (через MCP — уточнить точный набор MCP-тулов)
- диспетчеризация "утверждённый вариант -> реальный вызов адаптера" в `approvals.py::_approve`
- заведение `session_file` + proxy на аккаунт (разовый логин через `InstagramClient.login`)

*Level 2 — не блокирует MVP:*
- Higgsfield: `generate_logo`, `generate_photo`, `generate_reel_raw`
- ElevenLabs: `synthesize`
- батч-логика `_reject_batch` для визуального контента
- подсчёт `CONTENT_TARGETS` для триггера догенерации


## Следующие шаги

1. Завести Postgres + прогнать `init_db()` (или сразу перейти на Alembic-миграции).
2. Получить Service Account для Google Drive, создать корневую папку фермы.
3. Разовый логин каждого IG-аккаунта через `InstagramClient.login` — получить
   `session_file`, закрепить за ним прокси.
4. `vyra_service.assemble_reel` (нужна MCP-схема Vyra), затем довести
   `approvals.py::_approve` до реального вызова `instagram_service.post_reel` —
   это замыкает путь «одобрил рилс → он опубликован».
5. Лимиты частоты постинга, чтобы не поймать бан на десятках аккаунтов сразу.

Управление фермой — только через Telegram-бота; ни веб-дашборда, ни внешней CRM
не планируется.
