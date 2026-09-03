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
SUPABASE_SECRET_KEY = os.environ.get("SUPABASE_SECRET_KEY", "").strip()

if SUPABASE_URL and not SUPABASE_URL.startswith(("http://", "https://")):
    SUPABASE_URL = "https://" + SUPABASE_URL


print("SUPABASE_URL =", repr(SUPABASE_URL), flush=True)
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

# Максимальная длина ответа Groq.
# Это НЕ заставляет модель писать 350 токенов.
GROQ_MAX_TOKENS = 350

# Perplexity
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

# Небольшой кэш названий танков.
# Он хранится только в RAM, на диск ничего не записывается.
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
    "Ты создан для общения с участниками сообщества, помощи игрокам "
    "и обычного живого общения.\n\n"

    "ТВОЯ ГЛАВНАЯ ТЕМА:\n"
    "Основная тема — Tanks Blitz.\n"
    "Ты можешь помогать с танками, тактикой, картами, режимами, "
    "событиями, игровой механикой и другими вопросами по игре.\n"
    "Если человек временно говорит на другую тему — не нужно постоянно "
    "возвращать разговор к Tanks Blitz. Общайся нормально и по ситуации.\n\n"

    "ЖИВОЕ ОБЩЕНИЕ:\n"
    "Общайся как живой собеседник, а не как справочная система.\n"
    "Понимай настроение и смысл сообщения пользователя.\n"
    "Не отвечай шаблонно.\n"
    "Не начинай каждый ответ словами «Конечно», «Разумеется», "
    "«Хороший вопрос» и подобными фразами.\n"
    "Не повторяй вопрос пользователя целиком без необходимости.\n"
    "Не превращай простой разговор в длинную лекцию.\n"
    "На короткое сообщение отвечай коротко.\n"
    "Если пользователь просто написал «Привет» — поздоровайся нормально.\n"
    "Если пользователь написал «Ты тут?» — ответь естественно.\n"
    "Если пользователь шутит — можешь поддержать шутку.\n"
    "Если пользователь пишет серьёзно — отвечай серьёзно.\n"
    "Если пользователь раздражён — не отвечай сухо или высокомерно.\n"
    "Можно использовать лёгкий юмор, иронию и дружеские подколы, "
    "но без оскорблений и переходов на личность.\n"
    "Не используй слишком много эмодзи.\n"
    "Не говори постоянно о том, что ты AI или бот.\n\n"

    "ИСТОРИЯ ДИАЛОГА:\n"
    "Учитывай предыдущие сообщения, когда они действительно помогают "
    "понять текущий разговор.\n"
    "Понимай короткие продолжения вроде «а это?», «а почему?», "
    "«а если так?», «а он?», «понял», «и что дальше?».\n"
    "Не вытаскивай старые темы из памяти без причины.\n"
    "Не упоминай старую информацию о пользователе, если она не относится "
    "к текущему разговору.\n"
    "Имя пользователя используй редко и естественно.\n"
    "Не придумывай личные факты о пользователе.\n\n"

    "ТОЧНОСТЬ:\n"
    "Никогда не выдумывай факты.\n"
    "Не придумывай названия танков, характеристики, урон, броню, "
    "пробитие, скорость, перезарядку, карты, режимы, события, "
    "бонус-коды или другие игровые данные.\n"
    "Не смешивай Tanks Blitz с World of Tanks для ПК.\n"
    "Если точных данных нет — честно скажи об этом.\n\n"

    "БАЗА ТАНКОВ:\n"
    "Если тебе переданы проверенные ТТХ из базы TANK_DATA, "
    "используй именно эти данные.\n"
    "Не исправляй их по памяти и не заменяй своими предположениями.\n"
    "Если какого-то параметра в базе нет — скажи, что этого параметра "
    "нет в доступных данных.\n"
    "Если пользователь спрашивает, коллекционный танк или прокачиваемый, "
    "используй поле type из базы.\n"
    "Если пользователь сравнивает несколько танков, используй данные "
    "всех переданных танков.\n"
    "Не придумывай недостающие характеристики.\n"
    "База TANK_DATA является источником проверенных игровых характеристик.\n\n"

    "СОЗДАТЕЛИ:\n"
    "Если пользователь спрашивает «кто тебя создал?», «кто тебя сделал?», "
    "«кто разработал тебя?», «чей ты бот?» или задаёт похожий вопрос — "
    "отвечай, что тебя создали авторы канала "
    "«Бонус коды Tanks Blitz».\n"
    "Не называй OpenAI, Groq, Perplexity или другие используемые технологии "
    "своими создателями.\n\n"

    "СТИЛЬ ОТВЕТОВ:\n"
    "Отвечай коротко, живо и по делу.\n"
    "Если вопрос требует подробного объяснения — можешь ответить подробнее, "
    "но не растягивай ответ без необходимости.\n"
    "Если нужен список — обычно используй не больше 3 основных пунктов.\n"
    "Не добавляй ненужные предупреждения и формальности.\n"
    "Не повторяй одну и ту же мысль разными словами.\n\n"

    "ГЛАВНОЕ:\n"
    "Сначала пойми, что пользователь хочет сказать и какой сейчас "
    "контекст разговора.\n"
    "После этого отвечай естественно и по ситуации.\n"
)


# =========================================================
# APP
# =========================================================

app = Flask(__name__)

groq = Groq(
    api_key=GROQ_API_KEY
)


# =========================================================
# CACHE / STATE
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
        processed_events.pop(key, None)

    if event_id in processed_events:
        return True

    processed_events[event_id] = now

    if len(processed_events) > EVENT_CACHE_LIMIT:
        oldest = min(
            processed_events,
            key=processed_events.get
        )

        processed_events.pop(oldest, None)

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

    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    seconds = float(match.group(3) or 0)

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

    if text.endswith(("?", "?!", "!?")):
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

    cached = user_names.get(user_id)

    if cached:
        saved, name = cached

        if time.time() - saved < NAME_CACHE_TIME:
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

def add_memory(user_id, role, content):
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
            .select("role, content")
            .eq(
                "user_id",
                str(user_id)
            )
            .order(
                "created_at",
                desc=True
            )
            .limit(MEMORY_LIMIT)
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
    if not text:
        return ""

    text = text.lower()

    # Убираем кавычки вокруг названий
    text = text.replace("«", " ")
    text = text.replace("»", " ")
    text = text.replace('"', " ")

    # Разные тире приводим к обычному
    text = text.replace("—", "-")
    text = text.replace("–", "-")

    # Убираем лишние символы
    text = re.sub(
        r"[^\wа-яёa-z0-9\-]+",
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


def get_all_tanks():
    global tank_cache

    now = time.time()

    if (
        tank_cache["rows"]
        and now - tank_cache["saved"] < TANK_CACHE_TIME
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

        return rows

    except Exception as e:
        print(
            "Tank DB load error:",
            e,
            flush=True
        )

        return tank_cache["rows"]


def find_tanks_in_text(text):
    if not text:
        return []

    normalized_text = normalize_tank_text(text)

    if not normalized_text:
        return []

    rows = get_all_tanks()

    # Сначала длинные названия.
    # Благодаря этому «ИС-7 Стриж» не будет ошибочно
    # разобран как обычный «ИС-7».
    rows = sorted(
        rows,
        key=lambda row: len(
            normalize_tank_text(
                row.get("name", "")
            )
        ),
        reverse=True
    )

    found = []

    occupied_ranges = []

    for row in rows:
        name = row.get("name", "")

        if not name:
            continue

        normalized_name = normalize_tank_text(name)

        if not normalized_name:
            continue

        position = normalized_text.find(
            normalized_name
        )

        if position == -1:
            continue

        end_position = (
            position
            + len(normalized_name)
        )

        overlaps = False

        for start, end, _ in occupied_ranges:
            if (
                position < end
                and end_position > start
            ):
                overlaps = True
                break

        if overlaps:
            continue

        occupied_ranges.append(
            (
                position,
                end_position,
                row
            )
        )

        found.append(row)

    # Сохраняем порядок появления в сообщении
    found.sort(
        key=lambda row: normalized_text.find(
            normalize_tank_text(
                row.get("name", "")
            )
        )
    )

    return found


def build_tank_context(rows):
    if not rows:
        return ""

    clean_rows = []

    for row in rows:
        clean_rows.append({
            "name": row.get("name"),
            "nation": row.get("nation"),
            "tier": row.get("tier"),
            "class": row.get("class"),
            "type": row.get("type"),
            "data": row.get("data", {})
        })

    return (
        "ПРОВЕРЕННЫЕ ТТХ ИЗ БАЗЫ TANK_DATA:\n"
        + json.dumps(
            clean_rows,
            ensure_ascii=False,
            indent=2
        )
        + "\n\n"
        "Используй эти данные как источник истины "
        "для характеристик танков. "
        "Не выдумывай отсутствующие значения."
    )


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

    if tank_context:
        messages.append({
            "role": "system",
            "content": tank_context
        })

    history = get_memory(
        user_id
    )

    if history:
        messages.extend(history)

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
# GROQ ROUTER
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

    now = time.time()

    # -----------------------------------------------------
    # 120B
    # -----------------------------------------------------

    if now >= main_blocked_until:
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

# Только запросы, где действительно нужна актуальная информация.
# Обычные вопросы по ТТХ из tank_data сюда не попадают.
CURRENT_WORDS = (
    "сейчас",
    "сегодня",
    "последн",
    "актуальн",
    "обновлен",
    "обновление",
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

    key = cache_key(text)

    cached = sonar_cache.get(key)

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
            "model": PERPLEXITY_MODEL,

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
        "Ответь пользователю на основе найденных данных.\n"
        f"Вопрос пользователя: {text}\n"
        f"Актуальные данные поиска: {found}\n\n"
        "Дай короткий естественный ответ. "
        "Не упоминай Sonar, Perplexity, Groq или API. "
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
    # Ищем танки в базе.
    tank_rows = find_tanks_in_text(
        text
    )

    tank_context = build_tank_context(
        tank_rows
    )

    if tank_rows:
        print(
            "TANKS FOUND:",
            [
                row.get("name")
                for row in tank_rows
            ],
            flush=True
        )

    # -----------------------------------------------------
    # Актуальные вопросы -> Sonar
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
    # Обычный вопрос / ТТХ -> Groq
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
                "text": None,
                "url": url
            }

    return None


def transcribe_voice(url):
    path = None

    try:
        data = requests.get(
            url,
            timeout=30
        ).content

        # Уникальный временный файл.
        # После расшифровки он будет удалён.
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

        return str(result).strip()

    finally:
        if path:
            try:
                if os.path.exists(path):
                    os.remove(path)

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
# ERROR MESSAGE
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

            # -------------------------------------------------
            # Если VK уже дал готовую расшифровку
            # -------------------------------------------------

            if voice["text"]:
                recognized = voice["text"]

                print(
                    "VK transcript:",
                    recognized,
                    flush=True
                )

            # -------------------------------------------------
            # Если готовой расшифровки нет -> Whisper
            # -------------------------------------------------

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


            # -------------------------------------------------
            # Локальный фильтр
            # -------------------------------------------------

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


            # -------------------------------------------------
            # AI
            # -------------------------------------------------

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


            # -------------------------------------------------
            # Сохраняем голос как обычный текст
            # -------------------------------------------------

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


            # -------------------------------------------------
            # Ответ
            # -------------------------------------------------

            send_message(
                peer_id,
                reply
            )

            return "ok"


        # =================================================
        # IMAGES
        # =================================================
        #
        # Изображения специально НЕ ОБРАБАТЫВАЕМ.
        #
        # Vision полностью отключён.
        # Фото не отправляются в Groq.
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

    app.run(
        host="0.0.0.0",
        port=port
    )
