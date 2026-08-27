"""
Per-scenario creative briefs.

The old approach was one generic prompt for everything, with a stringified Python
dict pasted in. It produced technically-correct, lifeless copy: a bio and a Reels
script are different crafts and a shared prompt serves neither.

Each brief below carries the four things a good prompt needs and a generic one
lacks: the job, the hard format constraints of the surface, the failure mode to
avoid, and a concrete quality bar. Briefs are data, not code — edit them freely,
that is the intended way to tune output quality.
"""
from __future__ import annotations

from dataclasses import dataclass

from services.persona import PersonaCard


@dataclass(frozen=True)
class Brief:
    goal: str            # what a good result achieves
    constraints: str     # hard limits of the surface (length, format)
    avoid: str           # the specific way this scenario usually goes wrong
    quality_bar: str     # how the operator will judge it
    variants: int = 5    # how many options to offer for approval-by-number


BRIEFS: dict[str, Brief] = {
    "brand_name": Brief(
        goal="Придумать название бренда: короткое, произносимое вслух с первого раза, "
             "запоминающееся после одного прочтения.",
        constraints="1-2 слова. Латиница или кириллица по запросу. Без цифр и дефисов. "
                    "Должно читаться однозначно — если название можно прочесть двумя "
                    "способами, оно не годится.",
        avoid="Составные англицизмы уровня GlamShop / BeautyHub, слова-пустышки "
              "(Premium, Elite, Style), и названия, которые невозможно продиктовать по телефону.",
        quality_bar="Человек услышал один раз в рилсе — и смог найти в поиске.",
        variants=8,
    ),
    "bio": Brief(
        goal="Написать шапку профиля, которая за 2 секунды объясняет, кто это и зачем "
             "на него подписываться.",
        constraints="До 150 символов вместе с эмодзи и переносами. 3 строки максимум. "
                    "Последняя строка — призыв к действию или указание на ссылку.",
        avoid="Абстрактные достоинства («качество», «стиль», «для тебя»), перечисление "
              "всего сразу, и эмодзи вместо слов там, где нужен смысл.",
        quality_bar="Из шапки понятно, что человек получит, если подпишется — конкретно, "
                    "а не по настроению.",
    ),
    "reels_scripts": Brief(
        goal="Написать сценарий Reels: зацепка в первые 1.5 секунды, удержание в середине, "
             "внятный финал с действием.",
        constraints="15-45 секунд. Формат по репликам с таймкодами: [0-2с] реплика / что в кадре. "
                    "Первая фраза — не приветствие и не представление, а сразу крючок.",
        avoid="Начало с «Привет, ребята» и «сегодня я расскажу» — на этом теряется половина "
              "зрителей. Также: обещание в начале, которое ролик не выполняет.",
        quality_bar="Первую фразу хочется дослушать; в конце понятно, что делать дальше.",
    ),
    "voiceover_text": Brief(
        goal="Написать текст под озвучку — устную речь, а не письменную.",
        constraints="Короткие фразы, которые произносятся на одном дыхании. Без причастных "
                    "оборотов и вложенных придаточных. Числа словами. Уложиться в заданный "
                    "хронометраж из расчёта ~2.5 слова в секунду.",
        avoid="Книжный синтаксис, который невозможно прочитать вслух без запинки. Проверка "
              "простая: если фразу нельзя произнести не переводя дыхание — она длинная.",
        quality_bar="Прочитано вслух звучит как живая речь, а не как озвученная статья.",
        variants=3,
    ),
    "carousel": Brief(
        goal="Написать текст карусели: одна мысль на слайд, каждый слайд тянет пролистнуть дальше.",
        constraints="6-9 слайдов. Первый — обещание или вопрос. Последний — действие. "
                    "На слайд максимум 12 слов: остальное не читают. Формат: «Слайд N: текст».",
        avoid="Слайды-связки без содержания («итак», «а теперь»), и первый слайд, который "
              "описывает тему вместо того, чтобы обещать выгоду.",
        quality_bar="Каждый слайд можно вырезать и он останется осмысленным.",
        variants=3,
    ),
    "tg_post": Brief(
        goal="Написать пост в Telegram-канал — регистр другой, чем в Instagram: "
             "тут читают текст, а не смотрят картинку.",
        constraints="800-1500 знаков. Первая строка работает как заголовок в превью. "
                    "Абзацы по 2-3 строки. Без хэштегов.",
        avoid="Инстаграмный тон с эмодзи через слово и разрывами строк ради воздуха.",
        quality_bar="Дочитывается до конца без прокрутки по диагонали.",
        variants=3,
    ),
    "daily_story": Brief(
        goal="Текст для сторис со ссылкой на рилс — одна фраза, которая заставляет свайпнуть.",
        constraints="До 60 символов. Одна строка.",
        avoid="«Новый рилс уже на странице» — это не повод переходить.",
        quality_bar="Фраза создаёт любопытство, а не сообщает факт.",
    ),
    "logo": Brief(
        goal="Составить техническое задание на логотип для генератора изображений — "
             "не сам логотип, а точное описание, что рисовать.",
        constraints="Описание на английском (генераторы точнее его понимают): тип знака, "
                    "композиция, палитра в hex, настроение, стиль линий, фон. Без текста "
                    "внутри картинки — генераторы плохо рисуют буквы, надпись накладывается отдельно.",
        avoid="Расплывчатые эпитеты вроде modern / clean / professional — они не задают "
              "ничего. Каждое слово должно менять картинку.",
        quality_bar="По описанию два разных художника нарисуют похожее.",
        variants=4,
    ),
    "highlight_covers": Brief(
        goal="Описать набор обложек для закреплённых сторис — единый стиль на всю сетку.",
        constraints="Описание на английском для генератора. Общая палитра и композиция на все "
                    "обложки, различие только в центральном символе. Указать имя каждой рубрики.",
        avoid="Разнобой между обложками — они должны читаться как один комплект.",
        quality_bar="Стоят рядом в профиле и выглядят как один набор, а не как случайные картинки.",
        variants=3,
    ),
    "story_covers": Brief(
        goal="Описать баннеры-обложки сторис под рубрики аккаунта.",
        constraints="Описание на английском для генератора. Вертикаль 1080x1920, место под "
                    "текст оставлено в верхней трети.",
        avoid="Композиция, в которой некуда положить надпись.",
        quality_bar="Текст ляжет поверх и останется читаемым.",
        variants=3,
    ),
}

DEFAULT_BRIEF = Brief(
    goal="Выполнить запрос в стиле аккаунта.",
    constraints="Формат по смыслу задачи.",
    avoid="Обобщённые формулировки без конкретики.",
    quality_bar="Готово к публикации без правок.",
)

# Rules that apply to every scenario. Kept in one place so tone stays consistent
# across the whole account instead of drifting prompt by prompt.
HOUSE_RULES = """\
Общие правила для всех текстов:
- Пиши по-русски, если явно не сказано иначе.
- Конкретика вместо оценок: не «отличное качество», а что именно делает его отличным.
- Без канцелярита и рекламных штампов («широкий ассортимент», «по доступным ценам»,
  «не оставит равнодушным»).
- Варианты должны отличаться подходом, а не парой переставленных слов.
- Никаких пояснений и преамбул — только готовый текст."""


def build_system_prompt(
    persona: PersonaCard, scenario_id: str, learned: str = ""
) -> str:
    """
    Persona + house rules + the scenario's craft brief, plus whatever this account
    has taught the system (services/feedback.py).

    `learned` goes last on purpose: approved examples and repeated complaints are
    account-specific and should win over the generic brief above them.
    """
    brief = BRIEFS.get(scenario_id, DEFAULT_BRIEF)
    tail = f"\n\n{learned}" if learned else ""
    return f"""\
Ты — копирайтер, который ведёт один конкретный аккаунт в Instagram и пишет от его лица.

ПЕРСОНА
{persona.voice_guidance()}

ЗАДАЧА
{brief.goal}

ФОРМАТ И ОГРАНИЧЕНИЯ
{brief.constraints}

ТИПИЧНАЯ ОШИБКА В ЭТОЙ ЗАДАЧЕ — ИЗБЕГАЙ ЕЁ
{brief.avoid}

КРИТЕРИЙ ГОТОВНОСТИ
{brief.quality_bar}

{HOUSE_RULES}{tail}"""


def build_user_prompt(
    scenario_id: str,
    answers: dict[str, str],
    revision_notes: str | None = None,
    previous_attempt: str | None = None,
) -> str:
    """The concrete request. Answers render as readable Q&A, not a dict dump."""
    brief = BRIEFS.get(scenario_id, DEFAULT_BRIEF)
    lines = [f"Дай {brief.variants} разных варианта."]

    if answers:
        lines.append("\nВводные от заказчика:")
        lines += [f"- {q} → {a}" for q, a in answers.items()]

    if previous_attempt:
        lines.append(f"\nПрошлый вариант, который отклонили:\n{previous_attempt}")
    if revision_notes:
        lines.append(
            f"\nПричина отказа: «{revision_notes}»\n"
            "Это главное требование к новой попытке. Не повторяй прежнюю ошибку и "
            "не выдавай ту же идею с переставленными словами."
        )

    lines.append(
        "\nВарианты должны отличаться подходом — разный угол, разная структура, "
        "разная интонация. Не пять переформулировок одной мысли."
    )
    return "\n".join(lines)


def build_image_prompt(persona: PersonaCard, scene: str) -> str:
    """
    Image-generation prompt. The persona's appearance block goes in UNCHANGED —
    that verbatim reuse is the entire mechanism behind a consistent face.
    """
    return (
        f"{persona.appearance_prompt()}\n\n"
        f"Сцена: {scene}\n\n"
        "Фотореализм: естественный свет, реальная оптика, живая кожа с текстурой, "
        "без пластиковой гладкости и без симметричного «AI-лица». "
        "Кадр как со смартфона или репортажной камеры, не студийный рендер."
    )


# Structured-output schema: the approval mechanic needs numbered variants, so we
# constrain the response shape instead of parsing them back out of prose.
VARIANTS_SCHEMA = {
    "type": "object",
    "properties": {
        "variants": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "number": {"type": "integer", "description": "Номер варианта, начиная с 1"},
                    "angle": {"type": "string", "description": "Чем этот вариант отличается — в 3-5 словах"},
                    "text": {"type": "string", "description": "Готовый текст варианта"},
                },
                "required": ["number", "angle", "text"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["variants"],
    "additionalProperties": False,
}
