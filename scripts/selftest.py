"""
Self-test: pure logic, no network, no database, no keys.

Runs everything that can be checked without external services — recipe
determinism, persona parsing, prompt assembly, the feedback distiller, subtitle
timing, encryption, trigger routing. Fast enough to run on every change.

    python scripts/selftest.py
"""
import os, sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "x")
os.environ.setdefault("ANTHROPIC_API_KEY", "x")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://u:p@localhost/db")
os.environ.setdefault("VAULT_ENCRYPTION_KEY", "0DFtE0Xk8vBxJ9tQ7bYyH3pZ2mNcR5sW1aG6dK4uL8g=")
os.environ.setdefault("GOOGLE_SERVICE_ACCOUNT_JSON", "./secrets/sa.json")

ok = fail = 0
def check(name, cond, detail=""):
    global ok, fail
    if cond: ok += 1; print(f"  ok    {name}")
    else: fail += 1; print(f"  FAIL  {name} {detail}")

# --- uniquify: determinism and ranges ---
from services.uniquify_service import build_recipe
a1, a2, b1 = build_recipe(3, 0), build_recipe(3, 0), build_recipe(7, 0)
check("recipe детерминирован", a1 == a2)
check("recipe различается по аккаунтам", a1 != b1)
check("recipe различается по variant", build_recipe(3, 1) != a1)
check("mirror выключен по умолчанию", a1.mirror is False)
check("crop в диапазоне", 0.010 <= a1.crop_pct <= 0.035, a1.crop_pct)
check("speed близко к 1", 0.97 <= a1.speed <= 1.03, a1.speed)
check("describe работает", "crop" in a1.describe())

# --- persona ---
from services.persona import PersonaCard, card_from_answers, interview_for
card = card_from_answers({
    "display_name": "Аня", "age": "24 года", "appearance_lock": "блондинка",
    "tone": "дружелюбно", "niche": "уход", "audience": "девушки 20-30",
    "forbidden": "мат, обещания чуда",
})
check("age парсится из текста", card.age == 24, card.age)
check("forbidden -> список", card.forbidden == ["мат", "обещания чуда"], card.forbidden)
check("json round-trip", PersonaCard.from_json(card.to_json()) == card)
check("missing_fields пуст", card.missing_fields() == [], card.missing_fields())
check("brand интервью короче", len(interview_for("brand")) < len(interview_for("blogger")))
check("appearance_prompt дословен", card.appearance_lock in card.appearance_prompt())
try:
    PersonaCard().appearance_prompt(); check("пустой lock -> ошибка", False)
except ValueError: check("пустой lock -> ошибка", True)

# --- prompts ---
from services import prompts
sp = prompts.build_system_prompt(card, "reels_scripts")
check("в промпт попала персона", "Аня" in sp)
check("в промпт попал avoid", "Привет, ребята" in sp)
check("в промпт попали правила", "канцелярит" in sp)
up = prompts.build_user_prompt("bio", {"УТП?": "быстрая доставка"}, revision_notes="сухо")
check("ответы читаемы", "УТП? → быстрая доставка" in up)
check("замечание в промпте", "сухо" in up)
check("brief.variants учтён", "5 разных" in prompts.build_user_prompt("bio", {}))
check("brand_name даёт 8", "8 разных" in prompts.build_user_prompt("brand_name", {}))

# --- feedback ---
from services import feedback
rules = feedback._distil_rules(["Слишком сухо", "слишком сухо!", "добавь эмодзи"])
check("повтор -> правило", rules == ["слишком сухо"], rules)
check("одиночное отброшено", "добавь эмодзи" not in rules)
ctx = feedback.LearnedContext(examples=["пример"], rules=["не сухо"])
check("render непустой", "ОДОБРЕННОЕ" in feedback.render_for_prompt(ctx))
check("пустой render пуст", feedback.render_for_prompt(feedback.LearnedContext()) == "")

# --- editor ---
from services.editor_service import subtitles_from_script, _escape
subs = subtitles_from_script("Раз два три четыре пять шесть семь восемь", 10.0, max_chars=15)
check("субтитры нарезаны", len(subs) >= 2, len(subs))
check("субтитры покрывают ролик", abs(subs[-1].end - 10.0) < 0.01, subs[-1].end)
check("субтитры не пересекаются", all(subs[i].end <= subs[i+1].start + 1e-6 for i in range(len(subs)-1)))
check("escape экранирует", _escape("a:b'c") == r"a\:b\'c", _escape("a:b'c"))

# --- vault ---
from services import vault
check("шифрование round-trip", vault.decrypt(vault.encrypt("секрет")) == "секрет")

# --- triggers ---
from triggers import resolve_trigger
check("триггер сценария", resolve_trigger("сделай био")[1] == "bio")
check("триггер аккаунта", resolve_trigger("аккаунт 3")[0] == "account_switch")
check("uniquify НЕ в общем словаре", resolve_trigger("уникализировать")[1] is None)
check("мусор не матчится", resolve_trigger("абракадабра")[0] is None)

print(f"\nпройдено {ok}, провалено {fail}")
sys.exit(1 if fail else 0)
