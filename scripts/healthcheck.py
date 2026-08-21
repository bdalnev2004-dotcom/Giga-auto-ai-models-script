"""
Smoke test for every external dependency: is the key present, does it work, and
what does the account/subscription look like.

Deliberately does NOT import config.py — that module requires every secret at
import time, so a single missing key would abort the whole run. Here each check
is independent: a missing key reports SKIP and the rest still run.

Usage:  python scripts/healthcheck.py
"""
import asyncio
import json
import os
import sys

from dotenv import load_dotenv

load_dotenv()

OK, FAIL, SKIP = "OK", "FAIL", "SKIP"
results: list[tuple[str, str, str]] = []


def record(service: str, status: str, detail: str) -> None:
    results.append((service, status, detail))
    mark = {OK: "[ OK ]", FAIL: "[FAIL]", SKIP: "[SKIP]"}[status]
    print(f"{mark} {service:<16} {detail}", flush=True)


def require(*names: str) -> list[str] | None:
    """Returns the values, or None (after recording a SKIP) if any are missing."""
    missing = [n for n in names if not os.getenv(n)]
    if missing:
        return None
    return [os.environ[n] for n in names]


async def check_anthropic() -> None:
    key = require("ANTHROPIC_API_KEY")
    if key is None:
        record("Anthropic", SKIP, "ANTHROPIC_API_KEY не задан")
        return
    model = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")
    try:
        from anthropic import AsyncAnthropic

        client = AsyncAnthropic(api_key=key[0])
        resp = await client.messages.create(
            model=model,
            max_tokens=16,
            messages=[{"role": "user", "content": "ping"}],
        )
        used = resp.usage.input_tokens + resp.usage.output_tokens
        record("Anthropic", OK, f"модель {model} отвечает, {used} токенов на пинг")
    except Exception as e:
        record("Anthropic", FAIL, f"{type(e).__name__}: {e}")


async def check_telegram() -> None:
    token = require("TELEGRAM_BOT_TOKEN")
    if token is None:
        record("Telegram", SKIP, "TELEGRAM_BOT_TOKEN не задан")
        return
    try:
        import httpx

        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(f"https://api.telegram.org/bot{token[0]}/getMe")
        data = r.json()
        if not data.get("ok"):
            record("Telegram", FAIL, str(data.get("description", data)))
            return
        record("Telegram", OK, f"бот @{data['result']['username']}")
    except Exception as e:
        record("Telegram", FAIL, f"{type(e).__name__}: {e}")


async def check_postgres() -> None:
    url = require("DATABASE_URL")
    if url is None:
        record("Postgres", SKIP, "DATABASE_URL не задан")
        return
    try:
        from sqlalchemy.ext.asyncio import create_async_engine
        from sqlalchemy import text

        engine = create_async_engine(url[0])
        async with engine.connect() as conn:
            version = (await conn.execute(text("select version()"))).scalar_one()
            tables = (await conn.execute(text(
                "select count(*) from information_schema.tables "
                "where table_schema = 'public'"
            ))).scalar_one()
        await engine.dispose()
        record("Postgres", OK, f"{version.split(',')[0]}, таблиц: {tables}")
    except Exception as e:
        record("Postgres", FAIL, f"{type(e).__name__}: {e}")


async def check_redis() -> None:
    url = require("REDIS_URL")
    if url is None:
        record("Redis", SKIP, "REDIS_URL не задан — FSM переживёт рестарт только с ним")
        return
    try:
        import redis.asyncio as aioredis

        client = aioredis.from_url(url[0])
        await client.ping()
        info = await client.info("server")
        await client.aclose()
        record("Redis", OK, f"версия {info.get('redis_version', '?')}")
    except Exception as e:
        record("Redis", FAIL, f"{type(e).__name__}: {e}")


async def check_vault() -> None:
    key = require("VAULT_ENCRYPTION_KEY")
    if key is None:
        record("Vault", SKIP, "VAULT_ENCRYPTION_KEY не задан")
        return
    try:
        from cryptography.fernet import Fernet

        f = Fernet(key[0].encode())
        if f.decrypt(f.encrypt(b"probe")) != b"probe":
            raise ValueError("round-trip mismatch")
        record("Vault", OK, "ключ валиден, шифрование работает")
    except Exception as e:
        record("Vault", FAIL, f"ключ невалиден — {type(e).__name__}: {e}")


async def check_drive() -> None:
    path = require("GOOGLE_SERVICE_ACCOUNT_JSON")
    if path is None:
        record("Google Drive", SKIP, "GOOGLE_SERVICE_ACCOUNT_JSON не задан")
        return
    if not os.path.exists(path[0]):
        record("Google Drive", FAIL, f"файл не найден: {path[0]}")
        return
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        creds = service_account.Credentials.from_service_account_file(
            path[0], scopes=["https://www.googleapis.com/auth/drive"]
        )
        drive = build("drive", "v3", credentials=creds)
        root = os.getenv("GOOGLE_DRIVE_ROOT_FOLDER_ID")
        if root:
            meta = drive.files().get(fileId=root, fields="name").execute()
            record("Google Drive", OK, f"доступ есть, корень «{meta['name']}»")
        else:
            drive.files().list(pageSize=1, fields="files(id)").execute()
            record("Google Drive", OK, "доступ есть; GOOGLE_DRIVE_ROOT_FOLDER_ID не задан")
    except Exception as e:
        record("Google Drive", FAIL, f"{type(e).__name__}: {e}")


async def check_elevenlabs() -> None:
    key = require("ELEVENLABS_API_KEY")
    if key is None:
        record("ElevenLabs", SKIP, "ELEVENLABS_API_KEY не задан")
        return
    try:
        import httpx

        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(
                "https://api.elevenlabs.io/v1/user/subscription",
                headers={"xi-api-key": key[0]},
            )
        if r.status_code != 200:
            record("ElevenLabs", FAIL, f"HTTP {r.status_code}: {r.text[:120]}")
            return
        d = r.json()
        used, limit = d.get("character_count", 0), d.get("character_limit", 0)
        left = limit - used
        record("ElevenLabs", OK, f"тариф {d.get('tier', '?')}, осталось {left:,} символов".replace(",", " "))
    except Exception as e:
        record("ElevenLabs", FAIL, f"{type(e).__name__}: {e}")


async def check_higgsfield() -> None:
    # Higgsfield authenticates with an id:secret pair — config.py currently has a
    # single HIGGSFIELD_API_KEY field, so accept either shape here.
    key_id = os.getenv("HIGGSFIELD_API_KEY_ID")
    secret = os.getenv("HIGGSFIELD_API_KEY_SECRET")
    single = os.getenv("HIGGSFIELD_API_KEY")
    if not (key_id and secret) and not single:
        record("Higgsfield", SKIP, "HIGGSFIELD_API_KEY_ID/_SECRET не заданы")
        return
    auth = f"Key {key_id}:{secret}" if key_id and secret else f"Key {single}"
    try:
        import httpx

        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.get(
                "https://platform.higgsfield.ai/v1/models",
                headers={"Authorization": auth},
            )
        if r.status_code == 200:
            record("Higgsfield", OK, "ключ принят")
        elif r.status_code in (401, 403):
            record("Higgsfield", FAIL, f"ключ отклонён (HTTP {r.status_code})")
        else:
            record("Higgsfield", FAIL, f"HTTP {r.status_code}: {r.text[:120]}")
    except Exception as e:
        record("Higgsfield", FAIL, f"{type(e).__name__}: {e}")


async def check_hikerapi() -> None:
    key = require("HIKERAPI_ACCESS_KEY")
    if key is None:
        record("HikerAPI", SKIP, "не задан (опционален — только чтение статистики)")
        return
    try:
        import httpx

        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(
                "https://api.hikerapi.com/v1/user/by/username",
                params={"username": "instagram"},
                headers={"x-access-key": key[0]},
            )
        if r.status_code == 200:
            record("HikerAPI", OK, "ключ принят (чтение)")
        else:
            record("HikerAPI", FAIL, f"HTTP {r.status_code}: {r.text[:120]}")
    except Exception as e:
        record("HikerAPI", FAIL, f"{type(e).__name__}: {e}")


async def main() -> int:
    print("\nПроверка ключей и сервисов Giga Automation\n" + "─" * 60)
    await asyncio.gather(
        check_anthropic(), check_telegram(), check_postgres(), check_redis(),
        check_vault(), check_drive(), check_elevenlabs(), check_higgsfield(),
        check_hikerapi(),
    )

    print("─" * 60)
    ok = sum(1 for _, s, _ in results if s == OK)
    failed = [n for n, s, _ in results if s == FAIL]
    skipped = [n for n, s, _ in results if s == SKIP]
    print(f"Работает: {ok}   Ошибок: {len(failed)}   Не настроено: {len(skipped)}")
    if failed:
        print("Ошибки:      " + ", ".join(failed))
    if skipped:
        print("Не настроено: " + ", ".join(skipped))

    print(
        "\nНе проверяется автоматически:\n"
        "  Vyra   — авторизация только через браузер, серверной проверки нет\n"
        "  IG-аккаунты — логин дёргать нельзя, это триггерит защиту Instagram"
    )
    if "--json" in sys.argv:
        print("\n" + json.dumps(
            [{"service": n, "status": s, "detail": d} for n, s, d in results],
            ensure_ascii=False, indent=2,
        ))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
