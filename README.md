# Giga Automation — Telegram bot skeleton

Рабочий каркас Telegram-бота из мастер-документа "Giga Automation": единый бэкенд,
триггер-словарь, FSM-диалоги с уточняющими вопросами, аппрувы по номеру/эмодзи,
хранение в Google Drive по нумерованной структуре, зашифрованный волт для кредов,
ежедневные напоминания/публикация сторис.

**Объём публикации (уточнено): автоматический постинг — только Instagram Reels
через HikerAPI.** Фото/карусели/сторис можно генерировать и утверждать как контент
(остаются в Drive), но автопостинг у них не запускается. TikTok/YouTube/VK — вне
текущего объёма (Blotato-адаптер оставлен как заглушка на будущее, но никуда не
подключён).

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
  hikerapi_service.py      # Instagram: фото/рилс/сторис со ссылкой
  higgsfield_service.py    # фото/видео/лого
  elevenlabs_service.py    # озвучка
  vyra_service.py          # монтаж (через MCP)
  blotato_service.py       # TikTok
db/
  models.py                # Account, AccountPlatform, Credential, ContentItem, AuditLogEntry
  session.py                # async SQLAlchemy сессия
bot.py                    # entrypoint
```

## Запуск

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # заполнить токены/ключи
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"  # -> VAULT_ENCRYPTION_KEY
# поднять Postgres, указать DATABASE_URL
python bot.py
```

## Что уже работает, а что — заглушки

**Есть логика:**
- триггер-словарь и распознавание команд
- переключение аккаунта `/account N`
- генерик FSM-диалог с уточняющими вопросами (данные реально собираются и уходят в Claude)
- аппрув по номеру/списку номеров/эмодзи, включая отбраковку пачки
- создание нумерованной структуры папок в Drive
- шифрование кредов (Fernet)
- модель БД целиком под доменную модель из документа
- крон-джобы на 12:00/15:00/20:00

**Заглушки (`raise NotImplementedError` или `TODO`)** — реальные вызовы внешних API,
т.к. нужны боевые ключи и точные контракты эндпоинтов:
- HikerAPI: `post_photo`, `post_reel`, `post_story_with_link`
- Higgsfield: `generate_logo`, `generate_photo`, `generate_reel_raw`
- ElevenLabs: `synthesize`
- Vyra: `assemble_reel` (через MCP — уточнить точный набор MCP-тулов)
- Blotato: `post_video`
- диспетчеризация "утверждённый вариант -> нужный сервис" в `approvals.py::_approve`
- привязка `chat_id` к аккаунту для планировщика (`scheduler.py`)

## Следующие шаги

1. Завести Postgres + прогнать `init_db()` (или сразу перейти на Alembic-миграции).
2. Получить Service Account для Google Drive, создать корневую папку фермы.
3. Один за другим реализовать заглушки сервисов, начиная с HikerAPI (постинг —
   самая частая операция) и Higgsfield (без него ничего не сгенерировать).
4. Добавить обработку `create_brand` / `create_blogger` как полноценных
   multi-step сценариев, которые в конце создают запись `Account` +
   вызывают `drive_service.create_account_folder_tree`.
5. Дальше — веб-дашборд поверх того же бэкенда (доп. FastAPI-слой над теми же
   `services/*` и `db/models.py`, без дублирования логики).
