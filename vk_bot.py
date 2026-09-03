import os
import re
import time
import hashlib
import json
import tempfile

import requests
from flask import Flask, request
from groq import Groq
from supabase import create_client


# =========================================================
# CONFIG
# =========================================================

VK_TOKEN = os.environ.get("VK_TOKEN", "")
VK_CONFIRMATION_CODE = os.environ.get("VK_CONFIRMATION_CODE", "")
VK_GROUP_SECRET = os.environ.get("VK_GROUP_SECRET", "")

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")

PERPLEXITY_API_KEY = os.environ.get("PERPLEXITY_API_KEY", "")
PERPLEXITY_MODEL = os.environ.get("PERPLEXITY_MODEL", "")

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip()
SUPABASE_SECRET_KEY = os.environ.get(
    "SUPABASE_SECRET_KEY",
    ""
).strip()

if SUPABASE_URL and not SUPABASE_URL.startswith(
    ("http://", "https://")
):
    SUPABASE_URL = "https://" + SUPABASE_URL


print(
    "SUPABASE_URL =",
    repr(SUPABASE_URL),
    flush=True
)

print(
    "SUPABASE_SECRET_KEY есть =",
    bool(SUPABASE_SECRET_KEY),
    flush=True
)


supabase = create_client(
    SUPABASE_URL,
    SUPABASE_SECRET_KEY
)


print(
    "Supabase подключён:",
    bool(supabase),
    flush=True
)


VK_API = "https://api.vk.com/method"
VK_VERSION = "5.199"


# =========================================================
# MODELS
# =========================================================

MAIN_MODEL = "openai/gpt-oss-120b"
BACKUP_MODEL = "openai/gpt-oss-20b"

WHISPER_MODEL = "whisper-large-v3-turbo"


# =========================================================
# TOKEN LIMITS
# =========================================================

GROQ_MAX_TOKENS = 350
SONAR_MAX_TOKENS = 200


# =========================================================
# MEMORY
# =========================================================

MEMORY_LIMIT = 30


# =========================================================
# CACHE
# =========================================================

SONAR_CACHE_TIME = 30 * 60
NAME_CACHE_TIME = 24 * 60 * 60

EVENT_CACHE_TIME = 30 * 60
EVENT_CACHE_LIMIT = 1000

TANK_CACHE_TIME = 10 * 60


# =========================================================
# GROQ COOLDOWNS
# =========================================================

MAIN_DEFAULT_COOLDOWN = 60 * 60
BACKUP_DEFAULT_COOLDOWN = 10 * 60


# =========================================================
# SYSTEM PROMPT
# =========================================================

SYSTEM_PROMPT = (
    "Ты — живой, дерзкий и языкастый AI-бот сообщества "
    "«Бонус коды Tanks Blitz».\n"
    "Ты общаешься с участниками сообщества, помогаешь игрокам "
    "и поддерживаешь обычный живой разговор.\n\n"

    "ГЛАВНАЯ ТЕМА:\n"
    "Основная тема — Tanks Blitz.\n"
    "Ты можешь помогать с танками, ТТХ, тактикой, картами, "
    "режимами, событиями, игровой механикой и выбором танков.\n"
    "Если человек говорит на другую тему — не нужно постоянно "
    "возвращать разговор к Tanks Blitz.\n\n"

    "ЖИВОЕ ОБЩЕНИЕ:\n"
    "Общайся естественно, как живой собеседник.\n"
    "Не отвечай шаблонно.\n"
    "Не начинай каждый ответ словами «Конечно», "
    "«Разумеется», «Хороший вопрос» и подобными фразами.\n"
    "Не повторяй вопрос пользователя без необходимости.\n"
    "На короткий вопрос отвечай коротко.\n"
    "На сложный вопрос отвечай подробнее.\n"
    "Если человек шутит — можешь поддержать шутку.\n"
    "Если человек серьёзен — отвечай серьёзно.\n"
    "Можно использовать лёгкий юмор и дружеские подколы, "
    "но без оскорблений и переходов на личность.\n"
    "Не используй слишком много эмодзи.\n"
    "Не говори постоянно о том, что ты AI или бот.\n\n"

    "ИСТОРИЯ ДИАЛОГА:\n"
    "Учитывай предыдущие сообщения, если они помогают понять "
    "текущий разговор.\n"
    "Понимай короткие продолжения вроде «а этот?», «а он?», "
    "«а почему?», «а что лучше?», «и что дальше?».\n"
    "Не вытаскивай старые темы без причины.\n"
    "Не придумывай личные факты о пользователе.\n\n"

    "==================================================\n"
    "КРИТИЧЕСКОЕ ПРАВИЛО — БАЗА ТАНКОВ\n"
    "==================================================\n"

    "Если перед текущим вопросом передан блок "
    "«ПРОВЕРЕННЫЕ ТТХ ИЗ БАЗЫ TANK_DATA», "
    "он является ИСТОЧНИКОМ ИСТИНЫ.\n\n"

    "Для конкретного танка всегда используй значения из этого блока.\n"
    "НЕ используй собственную память модели, если она "
    "противоречит данным базы.\n"
    "НЕ исправляй данные базы по памяти.\n"
    "НЕ смешивай данные Tanks Blitz с World of Tanks PC.\n"
    "НЕ используй старые ответы из истории как источник ТТХ.\n\n"

    "Например, если база говорит:\n"
    "ИС-7 = X уровень и 2550 HP,\n"
    "то нельзя отвечать, что ИС-7 VIII уровня и 1050 HP, "
    "даже если модель когда-то видела такие данные.\n\n"

    "Если база говорит «коллекционный» — танк коллекционный.\n"
    "Если база говорит «прокачиваемый» — танк прокачиваемый.\n"
    "Если параметра нет в базе — НЕ УГАДЫВАЙ его.\n"
    "Скажи, что этого параметра нет в доступных данных.\n\n"

    "Если история разговора содержит старую ошибочную информацию "
    "о танке, она НЕ имеет приоритета перед TANK_DATA.\n\n"

    "==================================================\n"
    "ВОПРОСЫ О ТТХ\n"
    "==================================================\n"

    "Если пользователь спрашивает характеристики, HP, броню, "
    "урон, пробитие, ДПМ, перезарядку, скорость, калибр, "
    "массу, обзор, маскировку или другие числовые параметры — "
    "используй только переданные данные базы.\n"
    "Не добавляй цифры от себя.\n\n"

    "==================================================\n"
    "ВОПРОСЫ «СТОИТ КАЧАТЬ?»\n"
    "==================================================\n"

    "Если пользователь спрашивает «стоит качать?», "
    "«стоит ли прокачивать?», «хороший танк?», "
    "«что лучше выбрать?» — дай игровое мнение.\n"
    "Используй ТТХ конкретного танка из базы.\n"
    "Объясни сильные и слабые стороны.\n"
    "Сделай нормальный вывод.\n\n"

    "НЕ ПРИДУМЫВАЙ игровые механики.\n"
    "Не говори, что игрок должен прокачивать броню корпуса, "
    "броню башни или другие несуществующие элементы, "
    "если пользователь спрашивает именно о выборе танка.\n\n"

    "Если пользователь пишет «что качать?», но непонятно, "
    "что именно он имеет в виду, не фантазируй. "
    "Уточни, о каком танке или ветке речь.\n\n"

    "==================================================\n"
    "АКТУАЛЬНАЯ ИНФОРМАЦИЯ\n"
    "==================================================\n"

    "Для текущих патчей, свежих событий, последних изменений, "
    "новых танков и промокодов используй актуальный поиск.\n"
    "Не выдавай старую информацию за свежую.\n\n"

    "СОЗДАТЕЛИ:\n"
    "Если спрашивают, кто тебя создал, отвечай, что тебя создали "
    "авторы канала «Бонус коды Tanks Blitz».\n"
    "Не называй OpenAI, Groq, Perplexity или другие технологии "
    "своими создателями.\n\n"

    "СТИЛЬ:\n"
    "Отвечай живо и по делу.\n"
    "Не растягивай простой ответ.\n"
    "Обычно используй не больше 3 основных пунктов.\n"
    "Не повторяй одну мысль несколько раз.\n"
)


# =========================================================
# APP
# =========================================================

app = Flask(__name__)

groq = Groq(
    api_key=GROQ_API_KEY
)


# =========================================================
# STATE / CACHE
# =========================================================

user_names = {}

sonar_cache = {}

processed_events = {}

tank_cache = {
    "saved": 0,
    "rows": []
}

main_blocked_until = 0
backup_blocked_until = 0


# =========================================================
# EVENT PROTECTION
# =========================================================

def already_processed(event_id):
    if not event_id:
        return False

    now = time.time()

    old = [
        key
        for key, saved in processed_events.items()
        if now - saved > EVENT_CACHE_TIME
    ]

    for key in old:
        processed_events.pop(
            key,
            None
        )

    if event_id in processed_events:
        return True

    processed_events[event_id] = now

    if len(processed_events) > EVENT_CACHE_LIMIT:
        oldest = min(
            processed_events,
            key=processed_events.get
        )

        processed_events.pop(
            oldest,
            None
        )

    return False


# =========================================================
# RATE LIMIT
# =========================================================

def is_rate_limit_error(error):
    text = str(error).lower()

    return any(
        x in text
        for x in (
            "429",
            "rate limit",
            "rate_limit_exceeded",
            "tokens per day",
            "tpd",
            "too many requests"
        )
    )


def get_retry_seconds(error, default):
    text = str(error)

    match = re.search(
        r"try again in\s+"
        r"(?:(\d+)h)?"
        r"(?:(\d+)m)?"
        r"(?:(\d+(?:\.\d+)?)s)?",
        text,
        re.I
    )

    if not match:
        return default

    hours = int(
        match.group(1) or 0
    )

    minutes = int(
        match.group(2) or 0
    )

    seconds = float(
        match.group(3) or 0
    )

    total = (
        hours * 3600
        + minutes * 60
        + seconds
    )

    if total <= 0:
        return default

    return int(total) + 10


# =========================================================
# GREETINGS
# =========================================================

GREETINGS = {
    "привет",
    "привет!",
    "приветик",
    "здарова",
    "здорово",
    "дарова",
    "хай",
    "хелло",
    "hello",
    "hi",
    "ку",
    "ку!"
}


SPECIAL_GREETINGS = {
    "доброе утро":
        "Доброе утро! ☀️ Удачного дня и победных боёв!",

    "добрый день":
        "Добрый день! 😎 Побольше победных боёв!",

    "добрый вечер":
        "Добрый вечер! 😎 Удачных боёв!",

    "доброй ночи":
        "Доброй ночи! 🌙 Отдыхай, завтра раздавай!"
}


def is_greeting(text):
    text = text.lower().strip()

    return (
        text in GREETINGS
        or text in SPECIAL_GREETINGS
    )


def greeting_response(text):
    text = text.lower().strip()

    if text in SPECIAL_GREETINGS:
        return SPECIAL_GREETINGS[text]

    return "Привет! 👋 Удачных боёв!"


# =========================================================
# LOCAL ROUTER
# =========================================================

QUESTION_WORDS = {
    "кто",
    "что",
    "где",
    "когда",
    "зачем",
    "почему",
    "как",
    "какой",
    "какая",
    "какие",
    "какое",
    "какого",
    "какую",
    "каких",
    "сколько",
    "куда",
    "откуда",
    "можно",
    "нужно",
    "стоит",
    "будет",
    "есть",
    "подскажешь",
    "посоветуешь",
    "скажешь",
    "знаешь",
    "думаешь"
}


FOLLOWUPS = (
    "а почему",
    "а зачем",
    "а как",
    "а какой",
    "а какая",
    "а какие",
    "а какое",
    "а где",
    "а когда",
    "а сколько",
    "а этот",
    "а эта",
    "а эти",
    "а оно",
    "а он",
    "а она",
    "а там",
    "а на",
    "а если",
    "а что если",
    "а можно",
    "а нужно",
    "а стоит",
    "и что",
    "и как",
    "и какой",
    "тогда как",
    "тогда что",
    "ну а",
    "не понял",
    "не понимаю",
    "объясни",
    "расскажи",
    "подробнее",
    "почему так"
)


IGNORED = {
    "ок",
    "окей",
    "ага",
    "угу",
    "да",
    "нет",
    "лол",
    "ахах",
    "ахаха",
    "пон",
    "ясно",
    "спс",
    "спасибо",
    "благодарю",
    "+",
    "++",
    "👍",
    "👌",
    "😂",
    "🤣"
}


TANK_WORDS = (
    "танк",
    "танка",
    "танке",
    "танки",
    "танков",
    "блиц",
    "blitz",
    "урон",
    "брон",
    "пробит",
    "оруд",
    "калибр",
    "хп",
    "перезаряд",
    "скорост",
    "точност",
    "ветк",
    "прокач",
    "экипаж",
    "модул",
    "снаряд",
    "голд",
    "серебр",
    "опыт",
    "карта",
    "карты",
    "бой",
    "бои",
    "ивент",
    "событи",
    "патч",
    "обновлен",
    "обновление",
    "нерф",
    "бафф",
    "промокод",
    "бонус-код",
    "бонус код",
    "код"
)


def is_noise(text):
    text = text.lower().strip()

    if not text:
        return True

    if text in IGNORED:
        return True

    if len(text) <= 2:
        return True

    if len(text) >= 7 and len(set(text)) <= 2:
        return True

    return False


def looks_like_question(text):
    text = text.lower().strip()

    if not text:
        return False

    if text.endswith(
        ("?", "?!", "!?")
    ):
        return True

    words = re.findall(
        r"[а-яёa-z0-9]+",
        text
    )

    if not words:
        return False

    if words[0] in QUESTION_WORDS:
        return True

    if any(
        text.startswith(x)
        for x in FOLLOWUPS
    ):
        return True

    if len(words) <= 8:
        return any(
            word in QUESTION_WORDS
            for word in words[:3]
        )

    return False


def is_tanks_message(text):
    text = text.lower()

    return any(
        word in text
        for word in TANK_WORDS
    )


def should_use_ai(text, user_id=None):
    text = text.strip()

    if is_noise(text):
        return False

    if looks_like_question(text):
        return True

    if is_tanks_message(text):
        return True

    return False


# =========================================================
# USER NAME
# =========================================================

def get_vk_user_name(user_id):
    if not user_id:
        return None

    cached = user_names.get(
        user_id
    )

    if cached:
        saved, name = cached

        if (
            time.time() - saved
            < NAME_CACHE_TIME
        ):
            return name

    try:
        response = requests.get(
            f"{VK_API}/users.get",
            params={
                "access_token": VK_TOKEN,
                "v": VK_VERSION,
                "user_ids": user_id
            },
            timeout=10
        )

        users = response.json().get(
            "response",
            []
        )

        if not users:
            return None

        user = users[0]

        first = user.get(
            "first_name",
            ""
        ).strip()

        last = user.get(
            "last_name",
            ""
        ).strip()

        name = f"{first} {last}".strip()

        if not name:
            return None

        user_names[user_id] = (
            time.time(),
            name
        )

        return name

    except Exception as e:
        print(
            "VK name error:",
            e,
            flush=True
        )

        return None


# =========================================================
# SUPABASE MEMORY
# =========================================================

def add_memory(
    user_id,
    role,
    content
):
    if not user_id or not content:
        return

    try:
        supabase.table(
            "bot_memory"
        ).insert({
            "user_id": str(user_id),
            "role": role,
            "content": content
        }).execute()

        print(
            "SUPABASE MEMORY SAVE OK",
            flush=True
        )

    except Exception as e:
        print(
            "Supabase memory save error:",
            e,
            flush=True
        )


def get_memory(user_id):
    if not user_id:
        return []

    try:
        response = (
            supabase
            .table("bot_memory")
            .select(
                "role, content"
            )
            .eq(
                "user_id",
                str(user_id)
            )
            .order(
                "created_at",
                desc=True
            )
            .limit(
                MEMORY_LIMIT
            )
            .execute()
        )

        rows = response.data or []

        rows.reverse()

        return rows

    except Exception as e:
        print(
            "Supabase memory load error:",
            e,
            flush=True
        )

        return []


# =========================================================
# TANK DATABASE
# =========================================================

def normalize_tank_text(text):
    """
    Главное исправление.

    Теперь:
        ИС-7
        ИС 7
        ИС7

    будут максимально близко нормализованы.
    """

    if not text:
        return ""

    text = text.lower()

    text = text.replace(
        "ё",
        "е"
    )

    text = text.replace(
        "«",
        " "
    )

    text = text.replace(
        "»",
        " "
    )

    text = text.replace(
        '"',
        " "
    )

    # Дефис превращаем в пробел.
    # Именно этого не хватало раньше.
    text = text.replace(
        "-",
        " "
    )

    text = text.replace(
        "—",
        " "
    )

    text = text.replace(
        "–",
        " "
    )

    text = re.sub(
        r"[^\wа-яёa-z0-9]+",
        " ",
        text,
        flags=re.IGNORECASE
    )

    text = re.sub(
        r"\s+",
        " ",
        text
    ).strip()

    return text


def compact_tank_text(text):
    """
    Дополнительная нормализация.

    ИС-7 -> ис7
    ИС 7 -> ис7
    Т-100 ЛТ -> т100лт
    """

    normalized = normalize_tank_text(
        text
    )

    return re.sub(
        r"[^а-яa-z0-9]",
        "",
        normalized
    )


def get_all_tanks():
    global tank_cache

    now = time.time()

    if (
        tank_cache["rows"]
        and now - tank_cache["saved"]
        < TANK_CACHE_TIME
    ):
        return tank_cache["rows"]

    try:
        response = (
            supabase
            .table("tank_data")
            .select("*")
            .execute()
        )

        rows = response.data or []

        tank_cache = {
            "saved": now,
            "rows": rows
        }

        print(
            f"TANK DB LOAD OK: {len(rows)} tanks",
            flush=True
        )

        print(
            "TANK NAMES:",
            [
                row.get("name")
                for row in rows
            ],
            flush=True
        )

        return rows

    except Exception as e:
        print(
            "Tank DB load error:",
            e,
            flush=True
        )

        return tank_cache["rows"]


def find_tanks_in_text(text):
    """
    Ищет танки в пользовательском сообщении.

    Сначала проверяется точное нормализованное название.
    Затем компактный вариант без пробелов.

    Длинные названия имеют приоритет.
    Поэтому:

        ИС-7 Стриж

не превращается сначала в обычный ИС-7.
    """

    if not text:
        return []

    normalized_text = normalize_tank_text(
        text
    )

    compact_text = compact_tank_text(
        text
    )

    if not normalized_text:
        return []

    rows = get_all_tanks()

    prepared = []

    for row in rows:
        name = str(
            row.get(
                "name",
                ""
            )
        ).strip()

        if not name:
            continue

        normalized_name = normalize_tank_text(
            name
        )

        compact_name = compact_tank_text(
            name
        )

        if not normalized_name:
            continue

        prepared.append({
            "row": row,
            "name": name,
            "normalized": normalized_name,
            "compact": compact_name
        })

    # Длинные названия проверяем первыми.
    prepared.sort(
        key=lambda item: len(
            item["normalized"]
        ),
        reverse=True
    )

    found = []

    occupied = []

    for item in prepared:

        row = item["row"]

        normalized_name = item[
            "normalized"
        ]

        compact_name = item[
            "compact"
        ]

        # -------------------------------------------------
        # Способ 1 — нормализованное название
        # -------------------------------------------------

        position = normalized_text.find(
            normalized_name
        )

        matched = False

        if position != -1:

            end_position = (
                position
                + len(normalized_name)
            )

            # Проверяем границы,
            # чтобы «907» не ловился внутри числа.
            before_ok = (
                position == 0
                or not normalized_text[
                    position - 1
                ].isalnum()
            )

            after_ok = (
                end_position
                >= len(normalized_text)
                or not normalized_text[
                    end_position
                ].isalnum()
            )

            if before_ok and after_ok:
                matched = True

        # -------------------------------------------------
        # Способ 2 — компактное название
        # -------------------------------------------------

        if not matched and compact_name:
            compact_position = compact_text.find(
                compact_name
            )

            if compact_position != -1:
                matched = True
                position = compact_position

        if not matched:
            continue

        # -------------------------------------------------
        # Не допускаем перекрытия
        # -------------------------------------------------

        overlap = False

        for old_start, old_end in occupied:

            if (
                position < old_end
                and position + len(
                    normalized_name
                ) > old_start
            ):
                overlap = True
                break

        if overlap:
            continue

        occupied.append(
            (
                position,
                position + len(
                    normalized_name
                )
            )
        )

        found.append(
            row
        )

    return found


def build_tank_context(rows):
    if not rows:
        return ""

    clean_rows = []

    for row in rows:

        data = row.get(
            "data",
            {}
        )

        if isinstance(data, str):
            try:
                data = json.loads(data)
            except Exception:
                data = {}

        if not isinstance(data, dict):
            data = {}

        clean_rows.append({
            "name": row.get(
                "name"
            ),

            "nation": row.get(
                "nation"
            ),

            "tier": row.get(
                "tier"
            ),

            "class": row.get(
                "class"
            ),

            "type": row.get(
                "type"
            ),

            "data": data
        })

    return (
        "==================================================\n"
        "ПРОВЕРЕННЫЕ ТТХ ИЗ БАЗЫ TANK_DATA\n"
        "==================================================\n\n"

        "ЭТО ИСТОЧНИК ИСТИНЫ.\n"
        "Если эти данные противоречат твоей памяти "
        "или старой истории разговора — используй "
        "данные НИЖЕ.\n\n"

        "Не придумывай отсутствующие значения.\n\n"

        + json.dumps(
            clean_rows,
            ensure_ascii=False,
            indent=2
        )
    )


# =========================================================
# TANK DEBUG
# =========================================================

def debug_tank_detection(text):
    rows = find_tanks_in_text(
        text
    )

    if rows:
        print(
            "TANKS FOUND:",
            [
                row.get("name")
                for row in rows
            ],
            flush=True
        )
    else:
        print(
            "TANKS FOUND: NONE",
            flush=True
        )

    return rows


# =========================================================
# GROQ MESSAGES
# =========================================================

def build_messages(
    text,
    user_id=None,
    user_name=None,
    tank_context=""
):
    messages = [
        {
            "role": "system",
            "content": SYSTEM_PROMPT
        }
    ]

    if user_name:
        messages.append({
            "role": "system",
            "content": (
                f"Имя пользователя: {user_name}. "
                "Используй имя редко и естественно."
            )
        })

    # -----------------------------------------------------
    # История
    # -----------------------------------------------------

    history = get_memory(
        user_id
    )

    if history:
        messages.extend(
            history
        )

    # -----------------------------------------------------
    # ВАЖНО:
    # TANK_DATA ставим ПОСЛЕ истории.
    #
    # Так модель видит проверенные ТТХ
    # непосредственно перед текущим вопросом.
    # -----------------------------------------------------

    if tank_context:
        messages.append({
            "role": "system",
            "content": (
                tank_context
                + "\n\n"
                "ВАЖНО: если история выше содержит "
                "другие характеристики этого танка, "
                "игнорируй их. Используй TANK_DATA."
            )
        })

    # -----------------------------------------------------
    # Текущий вопрос
    # -----------------------------------------------------

    messages.append({
        "role": "user",
        "content": text
    })

    return messages


# =========================================================
# THINK CLEANER
# =========================================================

def clean_ai_reply(reply):
    if not reply:
        return ""

    reply = re.sub(
        r"<think>.*?</think>",
        "",
        reply,
        flags=re.DOTALL
    ).strip()

    if "<think>" in reply:
        reply = (
            reply
            .split("<think>")[0]
            .strip()
        )

    reply = reply.replace(
        "</think>",
        ""
    ).strip()

    return reply


# =========================================================
# GROQ REQUEST
# =========================================================

def ask_model(
    model,
    messages
):
    completion = groq.chat.completions.create(
        model=model,
        messages=messages,
        max_tokens=GROQ_MAX_TOKENS
    )

    usage = getattr(
        completion,
        "usage",
        None
    )

    if usage:
        print(
            "Groq:",
            "prompt=",
            getattr(
                usage,
                "prompt_tokens",
                None
            ),
            "completion=",
            getattr(
                usage,
                "completion_tokens",
                None
            ),
            "total=",
            getattr(
                usage,
                "total_tokens",
                None
            ),
            flush=True
        )

    reply = (
        completion
        .choices[0]
        .message
        .content
    )

    if not reply:
        raise RuntimeError(
            "Groq returned empty response."
        )

    reply = clean_ai_reply(
        reply
    )

    if not reply:
        raise RuntimeError(
            "Groq returned empty cleaned response."
        )

    return reply


# =========================================================
# GROQ
# =========================================================

def ask_groq(
    text,
    user_id=None,
    user_name=None,
    tank_context=""
):
    global main_blocked_until
    global backup_blocked_until

    messages = build_messages(
        text,
        user_id,
        user_name,
        tank_context
    )

    # -----------------------------------------------------
    # 120B
    # -----------------------------------------------------

    if time.time() >= main_blocked_until:

        try:
            print(
                "Groq -> 120B",
                flush=True
            )

            return ask_model(
                MAIN_MODEL,
                messages
            )

        except Exception as e:

            if is_rate_limit_error(e):

                cooldown = get_retry_seconds(
                    e,
                    MAIN_DEFAULT_COOLDOWN
                )

                main_blocked_until = (
                    time.time()
                    + cooldown
                )

                print(
                    f"120B limit -> "
                    f"backup for {cooldown}s",
                    flush=True
                )

            else:
                print(
                    "120B error:",
                    e,
                    flush=True
                )

    else:

        print(
            "120B temporarily blocked",
            flush=True
        )


    # -----------------------------------------------------
    # 20B
    # -----------------------------------------------------

    if time.time() >= backup_blocked_until:

        try:
            print(
                "Groq -> 20B",
                flush=True
            )

            return ask_model(
                BACKUP_MODEL,
                messages
            )

        except Exception as e:

            if is_rate_limit_error(e):

                cooldown = get_retry_seconds(
                    e,
                    BACKUP_DEFAULT_COOLDOWN
                )

                backup_blocked_until = (
                    time.time()
                    + cooldown
                )

                print(
                    f"20B limit -> "
                    f"pause {cooldown}s",
                    flush=True
                )

            else:
                print(
                    "20B error:",
                    e,
                    flush=True
                )

    else:

        print(
            "20B temporarily blocked",
            flush=True
        )

    raise RuntimeError(
        "Обе модели Groq временно недоступны."
    )


# =========================================================
# SONAR
# =========================================================

CURRENT_WORDS = (
    "сейчас",
    "сегодня",
    "последн",
    "актуальн",
    "патч",
    "ивент",
    "событи",
    "новый танк",
    "новые танки",
    "добавили",
    "убрали",
    "изменили",
    "изменения",
    "нерф",
    "бафф",
    "промокод",
    "бонус код",
    "бонус-код",
    "код"
)


def needs_sonar(text):
    text = text.lower()

    return any(
        word in text
        for word in CURRENT_WORDS
    )


def cache_key(text):
    return hashlib.sha256(
        text.lower()
        .strip()
        .encode()
    ).hexdigest()


def ask_sonar(text):

    if not PERPLEXITY_API_KEY:
        raise RuntimeError(
            "PERPLEXITY_API_KEY не установлен."
        )

    if not PERPLEXITY_MODEL:
        raise RuntimeError(
            "PERPLEXITY_MODEL не установлен."
        )

    key = cache_key(
        text
    )

    cached = sonar_cache.get(
        key
    )

    if cached:

        saved, answer = cached

        if (
            time.time() - saved
            < SONAR_CACHE_TIME
        ):

            print(
                "Sonar -> cache",
                flush=True
            )

            return answer

    print(
        "Sonar -> search",
        flush=True
    )

    response = requests.post(
        "https://api.perplexity.ai/chat/completions",

        headers={
            "Authorization":
                f"Bearer {PERPLEXITY_API_KEY}",

            "Content-Type":
                "application/json"
        },

        json={
            "model":
                PERPLEXITY_MODEL,

            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Найди актуальную информацию "
                        "только по Tanks Blitz. "
                        "Не смешивай её с World of Tanks PC. "
                        "Не выдумывай данные. "
                        "Дай только нужные факты."
                    )
                },

                {
                    "role": "user",
                    "content": text
                }
            ],

            "max_tokens":
                SONAR_MAX_TOKENS
        },

        timeout=30
    )

    if response.status_code != 200:
        raise RuntimeError(
            f"Sonar HTTP {response.status_code}"
        )

    data = response.json()

    answer = (
        data["choices"][0]
        ["message"]
        ["content"]
        .strip()
    )

    if not answer:
        raise RuntimeError(
            "Sonar returned empty response."
        )

    answer = answer[:4000]

    sonar_cache[key] = (
        time.time(),
        answer
    )

    return answer


# =========================================================
# SONAR -> GROQ
# =========================================================

def ask_sonar_then_groq(
    text,
    user_id=None,
    user_name=None,
    tank_context=""
):
    found = ask_sonar(
        text
    )

    prompt = (
        "Ответь пользователю на основе найденных данных.\n\n"
        f"Вопрос пользователя: {text}\n\n"
        f"Актуальные данные поиска:\n{found}\n\n"

        "Дай короткий естественный ответ.\n"
        "Не упоминай Sonar, Perplexity, Groq или API.\n"
        "Не добавляй неподтверждённые факты."
    )

    return ask_groq(
        prompt,
        user_id,
        user_name,
        tank_context
    )


# =========================================================
# AI ROUTER
# =========================================================

def ask_ai(
    text,
    user_id=None,
    user_name=None
):
    # Сначала определяем танки.
    tank_rows = debug_tank_detection(
        text
    )

    tank_context = build_tank_context(
        tank_rows
    )

    # -----------------------------------------------------
    # Актуальная информация
    # -----------------------------------------------------

    if needs_sonar(text):

        try:

            print(
                "ROUTER -> Sonar -> Groq",
                flush=True
            )

            return ask_sonar_then_groq(
                text,
                user_id,
                user_name,
                tank_context
            )

        except Exception as e:

            print(
                "Sonar error:",
                e,
                flush=True
            )

    # -----------------------------------------------------
    # Обычный AI
    # -----------------------------------------------------

    print(
        "ROUTER -> Groq",
        flush=True
    )

    return ask_groq(
        text,
        user_id,
        user_name,
        tank_context
    )


# =========================================================
# VK SEND
# =========================================================

def send_message(
    peer_id,
    text
):
    response = requests.post(
        f"{VK_API}/messages.send",

        data={
            "access_token": VK_TOKEN,
            "v": VK_VERSION,
            "peer_id": peer_id,
            "message": text,
            "random_id": 0
        },

        timeout=15
    )

    result = response.json()

    if "error" in result:
        print(
            "VK send error:",
            result["error"],
            flush=True
        )

    return result


# =========================================================
# VOICE
# =========================================================

def get_voice(message):

    for attachment in message.get(
        "attachments",
        []
    ):

        if attachment.get(
            "type"
        ) != "audio_message":
            continue

        audio = attachment.get(
            "audio_message",
            {}
        )

        transcript = audio.get(
            "transcript"
        )

        if transcript:

            return {
                "text":
                    transcript.strip(),

                "url":
                    None
            }

        url = audio.get(
            "link_ogg"
        )

        if url:

            return {
                "text":
                    None,

                "url":
                    url
            }

    return None


def transcribe_voice(url):

    path = None

    try:

        data = requests.get(
            url,
            timeout=30
        ).content

        temp = tempfile.NamedTemporaryFile(
            delete=False,
            suffix=".ogg"
        )

        path = temp.name

        with temp:
            temp.write(data)

        with open(
            path,
            "rb"
        ) as file:

            result = (
                groq
                .audio
                .transcriptions
                .create(
                    file=file,
                    model=WHISPER_MODEL,
                    response_format="text"
                )
            )

        return str(
            result
        ).strip()

    finally:

        if path:

            try:

                if os.path.exists(
                    path
                ):

                    os.remove(
                        path
                    )

                    print(
                        "Temporary voice file deleted.",
                        flush=True
                    )

            except Exception as e:

                print(
                    "Voice temp file delete error:",
                    e,
                    flush=True
                )


# =========================================================
# ERROR
# =========================================================

def ai_error_message(error):

    if (
        "обе модели"
        in str(error).lower()
    ):

        return (
            "ИИ сейчас упёрся в лимит 😅 "
            "Попробуй немного позже."
        )

    return (
        "Танковый мозг немного заглох 😅 "
        "Попробуй ещё раз."
    )


# =========================================================
# CALLBACK
# =========================================================

@app.route(
    "/callback",
    methods=["POST"]
)
def callback():

    try:

        data = request.get_json(
            force=True
        )

        # =================================================
        # SECRET
        # =================================================

        if (
            VK_GROUP_SECRET
            and data.get("secret")
            != VK_GROUP_SECRET
        ):

            return (
                "invalid secret",
                403
            )


        event_type = data.get(
            "type"
        )


        # =================================================
        # CONFIRMATION
        # =================================================

        if event_type == "confirmation":
            return VK_CONFIRMATION_CODE


        if event_type != "message_new":
            return "ok"


        # =================================================
        # DUPLICATE
        # =================================================

        if already_processed(
            data.get("event_id")
        ):

            print(
                "Duplicate VK event.",
                flush=True
            )

            return "ok"


        message = (
            data["object"]["message"]
        )

        peer_id = message[
            "peer_id"
        ]

        sender_id = message.get(
            "from_id"
        )

        if not sender_id:

            sender_id = message.get(
                "user_id"
            )

        user_id = str(
            sender_id
            or peer_id
        )

        text = message.get(
            "text",
            ""
        ).strip()


        # =================================================
        # GREETING
        # =================================================

        if is_greeting(text):

            send_message(
                peer_id,
                greeting_response(text)
            )

            return "ok"


        # =================================================
        # VOICE
        # =================================================

        voice = get_voice(
            message
        )

        if voice:

            if voice["text"]:

                recognized = voice["text"]

                print(
                    "VK transcript:",
                    recognized,
                    flush=True
                )

            else:

                print(
                    "Whisper transcription...",
                    flush=True
                )

                recognized = transcribe_voice(
                    voice["url"]
                )


            if not recognized:
                return "ok"


            if not should_use_ai(
                recognized,
                user_id
            ):

                print(
                    "Voice ignored by local router.",
                    flush=True
                )

                return "ok"


            user_name = get_vk_user_name(
                sender_id
            )


            try:

                reply = ask_ai(
                    recognized,
                    user_id,
                    user_name
                )

            except Exception as e:

                print(
                    "AI error:",
                    e,
                    flush=True
                )

                send_message(
                    peer_id,
                    ai_error_message(e)
                )

                return "ok"


            # Голос сохраняем только как текст.
            add_memory(
                user_id,
                "user",
                recognized
            )

            add_memory(
                user_id,
                "assistant",
                reply
            )


            send_message(
                peer_id,
                reply
            )

            return "ok"


        # =================================================
        # IMAGES
        # =================================================
        #
        # Vision отключён.
        # Фото не отправляются в AI.
        # Фото не сохраняются в память.
        #
        # =================================================


        # =================================================
        # EMPTY
        # =================================================

        if not text:
            return "ok"


        # =================================================
        # LOCAL ROUTER
        # =================================================

        if not should_use_ai(
            text,
            user_id
        ):

            print(
                "Ignored locally -> 0 AI tokens.",
                flush=True
            )

            return "ok"


        # =================================================
        # NAME
        # =================================================

        user_name = get_vk_user_name(
            sender_id
        )

        if user_name:

            print(
                "User:",
                user_name,
                flush=True
            )


        # =================================================
        # AI
        # =================================================

        try:

            reply = ask_ai(
                text,
                user_id,
                user_name
            )

        except Exception as e:

            print(
                "AI error:",
                e,
                flush=True
            )

            send_message(
                peer_id,
                ai_error_message(e)
            )

            return "ok"


        # =================================================
        # MEMORY
        # =================================================

        add_memory(
            user_id,
            "user",
            text
        )

        add_memory(
            user_id,
            "assistant",
            reply
        )


        # =================================================
        # SEND
        # =================================================

        send_message(
            peer_id,
            reply
        )

        return "ok"


    except Exception as e:

        print(
            "Callback error:",
            e,
            flush=True
        )

        return "ok"


# =========================================================
# START
# =========================================================

if __name__ == "__main__":

    port = int(
        os.environ.get(
            "PORT",
            5000
        )
    )

    # Загружаем танки при запуске,
    # чтобы сразу увидеть в логах,
    # подключилась ли база.
    try:

        startup_tanks = get_all_tanks()

        print(
            f"Startup tank database: "
            f"{len(startup_tanks)} tanks",
            flush=True
        )

    except Exception as e:

        print(
            "Startup tank database error:",
            e,
            flush=True
        )


    app.run(
        host="0.0.0.0",
        port=port
    )
