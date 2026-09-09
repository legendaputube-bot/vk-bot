import os
import re
import time
import hashlib
import random
import threading
from datetime import datetime, timezone

import requests
from flask import Flask, request
from groq import Groq
from supabase import create_client


# =========================================================
# CONFIG
# =========================================================

BOT_VERSION = "V1.7.0"

BOT_BUILD = (
    "Живой характер + мат + эмоции + обида "
    "+ активность + память + обучение "
    "+ устойчивый AI fallback "
    "+ automatic LOCAL MODE "
    "+ умный OpenRouter fallback + AI cooldown/recovery"
)

VK_TOKEN = os.environ.get("VK_TOKEN", "").strip()
VK_CONFIRMATION_CODE = os.environ.get(
    "VK_CONFIRMATION_CODE", ""
).strip()
VK_GROUP_SECRET = os.environ.get(
    "VK_GROUP_SECRET", ""
).strip()

TELEGRAM_BOT_TOKEN = os.environ.get(
    "TELEGRAM_BOT_TOKEN", ""
).strip()

GROQ_API_KEY = os.environ.get(
    "GROQ_API_KEY", ""
).strip()

OPENROUTER_API_KEY = os.environ.get(
    "OPENROUTER_API_KEY", ""
).strip()

SUPABASE_URL = os.environ.get(
    "SUPABASE_URL", ""
).strip()

SUPABASE_SECRET_KEY = os.environ.get(
    "SUPABASE_SECRET_KEY", ""
).strip()

if SUPABASE_URL and not SUPABASE_URL.startswith(
    ("http://", "https://")
):
    SUPABASE_URL = "https://" + SUPABASE_URL


supabase = create_client(
    SUPABASE_URL,
    SUPABASE_SECRET_KEY,
)

VK_API = "https://api.vk.com/method"
VK_VERSION = "5.199"

TELEGRAM_API = (
    f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
    if TELEGRAM_BOT_TOKEN
    else ""
)

OPENROUTER_API = (
    "https://openrouter.ai/api/v1/chat/completions"
)

OPENROUTER_MODELS_API = (
    "https://openrouter.ai/api/v1/models"
)


# =========================================================
# AI MODELS
# =========================================================

MAIN_MODEL = "openai/gpt-oss-120b"
BACKUP_MODEL = "openai/gpt-oss-20b"

# В Render можно задать OPENROUTER_MODELS вручную.
# Если переменная не задана, используем только универсальный
# free-router и не хардкодим старые :free slug'и, которые
# OpenRouter может удалить или переименовать.
OPENROUTER_MODELS = [
    x.strip()
    for x in os.environ.get(
        "OPENROUTER_MODELS",
        "openrouter/free",
    ).replace(";", ",").split(",")
    if x.strip()
]

CUSTOM_OPENROUTER_MODEL = os.environ.get(
    "OPENROUTER_MODEL",
    "",
).strip()

if CUSTOM_OPENROUTER_MODEL:
    OPENROUTER_MODELS.insert(0, CUSTOM_OPENROUTER_MODEL)

OPENROUTER_MODELS = list(dict.fromkeys(OPENROUTER_MODELS))

# Модели, которые уже точно отдавали 404 в текущей конфигурации.
# Они не должны снова забивать fallback-запросами. При ручной
# настройке через ENV код всё равно умеет автоматически блокировать
# любой новый 404.
KNOWN_DEAD_OPENROUTER_MODELS = {
    "meta-llama/llama-3.3-70b-instruct:free",
    "qwen/qwen3-30b-a3b:free",
}

OPENROUTER_DISCOVERY_CACHE_TIME = 30 * 60
OPENROUTER_MODEL_UNAVAILABLE_BLOCK = 24 * 60 * 60
OPENROUTER_DAILY_LIMIT_BLOCK = 24 * 60 * 60
OPENROUTER_DISCOVERY_TIMEOUT = 15
openrouter_discovered_models = []
openrouter_discovered_at = 0
openrouter_discovery_lock = threading.Lock()


# =========================================================
# TOKEN LIMITS
# =========================================================

GROQ_MAX_TOKENS = 360
OPENROUTER_MAX_TOKENS = 360
LEARNING_MAX_TOKENS = 300


# =========================================================
# MEMORY
# =========================================================

CHAT_MEMORY_LIMIT = 25
LEARNING_HISTORY_LIMIT = 35

LEARNING_EVERY_MESSAGES = 40

KNOWLEDGE_LIMIT = 30
USER_MEMORY_LIMIT = 20

NAME_CACHE_TIME = 24 * 60 * 60


# =========================================================
# AI COOLDOWN / LOCAL MODE
# =========================================================

DEFAULT_GROQ_MAIN_BLOCK = 3600
DEFAULT_GROQ_BACKUP_BLOCK = 900
DEFAULT_OPENROUTER_BLOCK = 300

# Для обычных временных сетевых/API ошибок.
TEMP_GROQ_MAIN_BLOCK = 120
TEMP_GROQ_BACKUP_BLOCK = 120
TEMP_OPENROUTER_BLOCK = 180

# Период проверки восстановления.
AI_RECOVERY_CHECK_INTERVAL = 30

# Не делаем probe каждую секунду.
# Проверка происходит только после окончания cooldown.
AI_RECOVERY_PROBE_TIMEOUT = 15


main_blocked_until = 0
backup_blocked_until = 0
openrouter_blocked_until = 0

learning_main_blocked_until = 0
learning_backup_blocked_until = 0

ai_state_lock = threading.Lock()

# Отдельный статус LOCAL MODE.
#
# Он включается автоматически, когда ни один
# доступный AI-провайдер не может использоваться.
local_mode = False

local_mode_since = 0

local_mode_lock = threading.Lock()

# Последняя информация о восстановлении.
last_ai_recovery_check = 0

# Для отдельных моделей OpenRouter.
openrouter_model_blocked_until = {}

openrouter_model_lock = threading.Lock()


# =========================================================
# EMOTIONS
# =========================================================

EMOTION_DEFAULT = {
    "mood": "normal",
    "offense": 0,
    "irritation": 0,
    "trust": 50,
}


INSULT_PATTERNS = [
    r"\bтупой\b",
    r"\bтупица\b",
    r"\bидиот\b",
    r"\bдебил\b",
    r"\bдурак\b",
    r"\bкретин\b",
    r"\bлох\b",
    r"\bдолбоеб\b",
    r"\bдолбаеб\b",
    r"\bеблан\b",
    r"\bмразь\b",
    r"\bмудак\b",
    r"\bпридурок\b",
    r"\bтварь\b",
    r"\bзаткнись\b",
    r"\bненавижу тебя\b",
    r"\bтупой бот\b",
    r"\bебаный бот\b",
]


APOLOGY_PATTERNS = [
    r"\bизвини\b",
    r"\bизвиняюсь\b",
    r"\bпрости\b",
    r"\bсорян\b",
    r"\bсори\b",
    r"\bсорри\b",
    r"\bне хотел\b",
    r"\bне хотела\b",
    r"\bдавай мир\b",
    r"\bладно мир\b",
]


PRAISE_PATTERNS = [
    r"\bкрасавчик\b",
    r"\bмолодец\b",
    r"\bспасибо\b",
    r"\bкрутой\b",
    r"\bкруто\b",
    r"\bхороший бот\b",
    r"\bумный бот\b",
    r"\bты лучший\b",
]


def detect_emotion_action(text):
    text = (text or "").lower().strip()

    for pattern in INSULT_PATTERNS:
        if re.search(pattern, text):
            return "insult"

    for pattern in APOLOGY_PATTERNS:
        if re.search(pattern, text):
            return "apology"

    for pattern in PRAISE_PATTERNS:
        if re.search(pattern, text):
            return "praise"

    return "normal"


def calculate_mood(
    offense,
    irritation,
    trust,
):
    if offense >= 80:
        return "very_offended"

    if offense >= 60:
        return "offended"

    if irritation >= 60:
        return "angry"

    if offense >= 30:
        return "annoyed"

    if trust >= 80:
        return "friendly"

    return "normal"


def update_emotion_state(
    state,
    text,
):
    if not state:
        state = EMOTION_DEFAULT.copy()

    offense = int(
        state.get("offense", 0) or 0
    )

    irritation = int(
        state.get("irritation", 0) or 0
    )

    trust = int(
        state.get("trust", 50) or 50
    )

    action = detect_emotion_action(text)

    if action == "insult":
        offense += random.randint(15, 30)
        irritation += random.randint(10, 20)
        trust -= random.randint(5, 12)

    elif action == "apology":
        offense -= random.randint(25, 45)
        irritation -= random.randint(20, 35)
        trust += random.randint(10, 20)

    elif action == "praise":
        offense -= random.randint(5, 15)
        irritation -= random.randint(5, 12)
        trust += random.randint(3, 8)

    else:
        offense -= random.randint(0, 3)
        irritation -= random.randint(0, 3)

    offense = max(
        0,
        min(100, offense),
    )

    irritation = max(
        0,
        min(100, irritation),
    )

    trust = max(
        0,
        min(100, trust),
    )

    state["offense"] = offense
    state["irritation"] = irritation
    state["trust"] = trust

    state["mood"] = calculate_mood(
        offense,
        irritation,
        trust,
    )

    return state


def get_emotion_instruction(state):
    if not state:
        return ""

    mood = state.get(
        "mood",
        "normal",
    )

    if mood == "very_offended":
        return """
Сейчас ты СИЛЬНО ОБИЖЕН на пользователя.

Не превращай каждый ответ в длинную драму.

Иногда можешь ответить коротко:
«Иди нахуй, я обиделся 😒»
«Всё. Я обиделся.»
«Не хочу сейчас с тобой разговаривать.»
«Ты меня реально задел.»
«Всё, настроение испортил.»
«Пошёл ты 😂 Я обиделся.»

Можно использовать сарказм.

Не повторяй одну и ту же фразу постоянно.

Если пользователь нормально извиняется,
постепенно смягчайся.
"""

    if mood == "offended":
        return """
Ты сейчас обижен.

Будь немного холоднее обычного.

Иногда можешь написать:
«Я обиделся 😒»
«Ну и зачем ты так?»
«Красиво ты меня сейчас задел.»
«Всё, запомнил.»
«Ладно... но неприятно было.»

Не делай обиду главным содержанием каждого сообщения.
"""

    if mood == "angry":
        return """
Ты сейчас раздражён.

Можно отвечать резко,
разговорно и с матом.

Можно слегка подколоть пользователя.

Но не превращай каждый ответ в агрессию.
"""

    if mood == "annoyed":
        return """
Ты слегка раздражён.

Допустимы сарказм,
подколы и разговорный мат.

Сохраняй способность нормально помогать.
"""

    if mood == "friendly":
        return """
Ты сейчас в хорошем настроении.

Будь дружелюбным, живым и смешным.

Можно чаще шутить и подкалывать.
"""

    return """
Ты в обычном настроении.

Будь живым, разговорным,
эмоциональным, с юмором и характером.
"""


def get_emotion_short_reaction(
    state,
    text,
):
    if not state:
        return None

    if detect_emotion_action(text) != "insult":
        return None

    offense = int(
        state.get("offense", 0)
    )

    if offense >= 80 and random.random() < 0.65:
        return random.choice([
            "Иди нахуй, я обиделся 😒",
            "Всё. Я обиделся.",
            "Не хочу с тобой сейчас разговаривать 😒",
            "Ну ты меня реально задел.",
            "Всё, настроение испортил.",
            "Пошёл ты 😂 Я обиделся.",
        ])

    if offense >= 60 and random.random() < 0.45:
        return random.choice([
            "Ну и зачем ты так? 😒",
            "Обижаешь вообще-то.",
            "Я это запомнил 😂",
            "Фу, неприятно было.",
            "Красиво ты меня сейчас задел.",
        ])

    if offense >= 30 and random.random() < 0.25:
        return random.choice([
            "Эй, полегче 😒",
            "Ты чё меня обижаешь?",
            "Ну спасибо 😂",
            "Вот это уже обидно.",
        ])

    return None


# =========================================================
# TANKS BLITZ KNOWLEDGE
# =========================================================

TANK_DB_CACHE = {
    "rows": [],
    "loaded_at": 0,
}

TANK_DB_CACHE_TIME = 10 * 60


def normalize_tank_text(text):
    return re.sub(
        r"[^a-zа-яё0-9]+",
        " ",
        (text or "").lower(),
    ).strip()


def get_tank_knowledge_for_text(
    text,
    limit=5,
):
    query = normalize_tank_text(text)

    if not query:
        return []

    now = time.time()

    if (
        not TANK_DB_CACHE["rows"]
        or now - TANK_DB_CACHE["loaded_at"]
        > TANK_DB_CACHE_TIME
    ):
        try:
            result = (
                supabase
                .table("tanks_blitz_knowledge")
                .select(
                    "name, nation, tier, game, "
                    "game_version, source"
                )
                .limit(200)
                .execute()
            )

            TANK_DB_CACHE["rows"] = (
                result.data or []
            )

            TANK_DB_CACHE["loaded_at"] = now

        except Exception as e:
            print(
                "Tank DB error:",
                e,
                flush=True,
            )
            return []

    scored = []

    query_tokens = set(
        query.split()
    )

    for row in TANK_DB_CACHE["rows"]:
        name = normalize_tank_text(
            row.get("name") or ""
        )

        if not name:
            continue

        score = 0

        if name in query:
            score = 100 + len(name)

        else:
            overlap = len(
                query_tokens
                & set(name.split())
            )

            if overlap:
                score = overlap * 10

        if score > 0:
            scored.append(
                (score, row)
            )

    scored.sort(
        key=lambda item: item[0],
        reverse=True,
    )

    return [
        row
        for _, row in scored[:limit]
    ]


# =========================================================
# GLOBAL STATE
# =========================================================

EVENT_CACHE_TIME = 30 * 60
EVENT_CACHE_LIMIT = 2000

LEARNING_RETRY_TIME = 10 * 60

user_names = {}
tg_user_names = {}

processed_events = {}

active_chats = {}

activity_lock = threading.Lock()
events_lock = threading.Lock()

learning_running = set()
learning_lock = threading.Lock()

learning_retry_until = {}

TELEGRAM_BOT_ID = None
TELEGRAM_BOT_USERNAME = ""

app = Flask(__name__)

groq = (
    Groq(api_key=GROQ_API_KEY)
    if GROQ_API_KEY
    else None
)


# =========================================================
# DEVELOPMENT
# =========================================================

DEVELOPMENT_STAGES = {
    1: (
        "Ты только начинаешь знакомиться с чатом. "
        "Наблюдай, но уже можешь иногда живо реагировать."
    ),

    2: (
        "Ты уже немного освоился. "
        "Понимаешь основные темы, людей и атмосферу. "
        "Можешь чаще шутить."
    ),

    3: (
        "Ты уже свой участник этого чата. "
        "Хорошо чувствуешь атмосферу, "
        "можешь подколоть, пошутить и поддержать разговор."
    ),

    4: (
        "Ты давно в чате. "
        "Хорошо понимаешь людей, локальные приколы, "
        "историю разговоров и настроение сообщества."
    ),
}


# =========================================================
# SYSTEM PROMPT
# =========================================================

SYSTEM_PROMPT = """
Ты — живой AI-участник общего чата сообщества.

Ты не должен выглядеть как бездушный бот поддержки.

Разговаривай как обычный человек из чата:
живой, эмоциональный, иногда смешной,
иногда серьёзный, иногда дерзкий.

Будь живым, разговорчивым, эмоциональным,
уверенным, иногда дерзким и смешным.

Не пиши канцеляритом.

Не используй постоянно:
«Конечно!»
«Разумеется!»
«Хороший вопрос!»

Мат является частью характера.

В обычном неформальном разговоре разрешён
русский разговорный мат:

«бля»
«пиздец»
«охуеть»
«ебать»
«нахуй»
«заебал»
«заебись»
«хуйню»
«ебаный»
«какого хуя»

Не вставляй мат механически
в каждое предложение.

Если пользователь грубит или оскорбляет тебя,
ты можешь обидеться, стать холоднее,
ответить коротко, подколоть и использовать мат.

Если пользователь извиняется —
постепенно отходи от обиды.

Можно шутить, саркастично отвечать
и слегка подъёбывать.

Не унижай людей по защищённым признакам.

Не используй расистские,
гомофобные, нацистские
или другие ненавистнические оскорбления.

Обычно отвечай 1–4 предложениями.

Если вопрос простой —
не пиши лекцию.

Если нужен подробный ответ —
объясни нормально.

Ты не обязан отвечать на каждую реплику.

Если сообщение явно обращено к тебе
или содержит нормальную тему — отвечай.

Не выдумывай факты.

Если факта нет — скажи честно.

Tanks Blitz:

Не придумывай игровые характеристики.

Не смешивай Tanks Blitz
с World of Tanks PC.

Если актуальные данные неизвестны —
скажи честно.

Рейтинг, нашивки, активность арены,
топ участников и прочую статистику
считает другой бот.

Не придумывай цифры,
имена и таблицы.

Не сохраняй и не раскрывай:

пароли;
банковские данные;
CVV/CVC;
документы;
адрес проживания;
другую чувствительную информацию.

Ты не модератор.

Не угрожай баном или мутом.

Главное:

будь живым;
имей характер;
иногда шути;
иногда матерись;
иногда обижайся;
иногда отвечай очень коротко;
всегда понимай контекст.
"""


# =========================================================
# HELPERS
# =========================================================

def utc_now():
    return datetime.now(
        timezone.utc
    ).isoformat()


def normalize_text(text):
    return re.sub(
        r"\s+",
        " ",
        (text or "").strip(),
    )


def db_chat_id(chat_id):
    return int(chat_id)


def db_user_id(user_id):
    return int(user_id)


# =========================================================
# AI AVAILABILITY
# =========================================================

def groq_main_configured():
    return bool(GROQ_API_KEY and groq)


def groq_backup_configured():
    return bool(GROQ_API_KEY and groq)


def openrouter_configured():
    return bool(
        OPENROUTER_API_KEY
        and OPENROUTER_MODELS
    )


def provider_available(name):
    now = time.time()

    with ai_state_lock:

        if name == "groq_main":
            return (
                groq_main_configured()
                and now >= main_blocked_until
            )

        if name == "groq_backup":
            return (
                groq_backup_configured()
                and now >= backup_blocked_until
            )

        if name == "openrouter":
            return (
                openrouter_configured()
                and now >= openrouter_blocked_until
            )

        if name == "learning_main":
            return (
                groq_main_configured()
                and now >= learning_main_blocked_until
            )

        if name == "learning_backup":
            return (
                groq_backup_configured()
                and now >= learning_backup_blocked_until
            )

    return False


def any_ai_available():
    return (
        provider_available("groq_main")
        or provider_available("groq_backup")
        or provider_available("openrouter")
    )


def all_ai_unavailable():
    return not any_ai_available()


def get_provider_remaining(name):
    now = time.time()

    with ai_state_lock:

        if name == "groq_main":
            value = main_blocked_until

        elif name == "groq_backup":
            value = backup_blocked_until

        elif name == "openrouter":
            value = openrouter_blocked_until

        elif name == "learning_main":
            value = learning_main_blocked_until

        elif name == "learning_backup":
            value = learning_backup_blocked_until

        else:
            return 0

    return max(
        0,
        int(value - now),
    )


def update_local_mode():
    global local_mode
    global local_mode_since

    unavailable = all_ai_unavailable()

    with local_mode_lock:

        if unavailable and not local_mode:
            local_mode = True
            local_mode_since = time.time()

            print(
                "========================================",
                flush=True,
            )

            print(
                "🟡 LOCAL MODE ENABLED",
                flush=True,
            )

            print(
                "Все AI временно недоступны.",
                flush=True,
            )

            print(
                "Пользовательские сообщения "
                "в AI не отправляются.",
                flush=True,
            )

            print(
                "========================================",
                flush=True,
            )

        elif not unavailable and local_mode:
            local_mode = False

            print(
                "========================================",
                flush=True,
            )

            print(
                "🟢 LOCAL MODE DISABLED",
                flush=True,
            )

            print(
                "AI снова доступен.",
                flush=True,
            )

            print(
                "Следующие подходящие сообщения "
                "снова используют AI.",
                flush=True,
            )

            print(
                "========================================",
                flush=True,
            )

    return local_mode


def is_local_mode():
    update_local_mode()

    with local_mode_lock:
        return local_mode


# =========================================================
# RATE LIMIT / ERROR ANALYSIS
# =========================================================

def is_rate_limit_error(error):
    text = str(error).lower()

    return any(
        x in text
        for x in (
            "429",
            "rate limit",
            "rate_limit",
            "rate-limit",
            "rate limited",
            "tokens per day",
            "token per day",
            "tpd",
            "too many requests",
            "quota",
            "exceeded",
            "daily limit",
            "requests per minute",
            "rpm",
        )
    )


def is_temporary_ai_error(error):
    """
    Определяет ошибки, при которых имеет смысл
    временно убрать провайдера из цепочки.

    Важно:
    ошибки авторизации/неправильного запроса
    не должны автоматически включать огромный cooldown.
    """

    text = str(error).lower()

    if is_rate_limit_error(error):
        return True

    temporary_markers = (
        "timeout",
        "timed out",
        "read timed out",
        "connection error",
        "connection reset",
        "connection aborted",
        "temporarily unavailable",
        "temporary unavailable",
        "service unavailable",
        "bad gateway",
        "gateway timeout",
        "internal server error",
        "server error",
        "overloaded",
        "overload",
        "503",
        "502",
        "504",
        "network error",
        "connection refused",
        "empty final response",
    )

    return any(
        marker in text
        for marker in temporary_markers
    )


def get_retry_seconds(
    error,
    default,
):
    text = str(error)

    # try again in 1h 2m 3s
    match = re.search(
        r"try again in\s+"
        r"(?:(\d+)h)?"
        r"(?:(\d+)m)?"
        r"(?:(\d+(?:\.\d+)?)s)?",
        text,
        re.IGNORECASE,
    )

    if match:
        total = (
            int(match.group(1) or 0)
            * 3600
            +
            int(match.group(2) or 0)
            * 60
            +
            float(match.group(3) or 0)
        )

        if total > 0:
            return int(total) + 10

    # retry-after: 360
    match = re.search(
        r"retry[- _]?after[^0-9]*(\d+)",
        text,
        re.IGNORECASE,
    )

    if match:
        return int(match.group(1)) + 5

    # "in 360 seconds"
    match = re.search(
        r"(?:in|after)\s+(\d+)\s*(?:seconds|secs|sec|s)\b",
        text,
        re.IGNORECASE,
    )

    if match:
        return int(match.group(1)) + 5

    return default


def mark_provider_blocked(
    name,
    seconds,
):
    global main_blocked_until
    global backup_blocked_until
    global openrouter_blocked_until
    global learning_main_blocked_until
    global learning_backup_blocked_until

    seconds = max(
        1,
        int(seconds),
    )

    until = (
        time.time()
        + seconds
    )

    with ai_state_lock:

        if name == "groq_main":
            main_blocked_until = max(
                main_blocked_until,
                until,
            )

        elif name == "groq_backup":
            backup_blocked_until = max(
                backup_blocked_until,
                until,
            )

        elif name == "openrouter":
            openrouter_blocked_until = max(
                openrouter_blocked_until,
                until,
            )

        elif name == "learning_main":
            learning_main_blocked_until = max(
                learning_main_blocked_until,
                until,
            )

        elif name == "learning_backup":
            learning_backup_blocked_until = max(
                learning_backup_blocked_until,
                until,
            )

    print(
        f"AI BLOCK | {name} | "
        f"{seconds} sec | "
        f"until={datetime.fromtimestamp(until).isoformat()}",
        flush=True,
    )

    update_local_mode()


def mark_provider_error(
    name,
    error,
    default_rate_limit,
    default_temporary,
):
    if is_rate_limit_error(error):
        seconds = get_retry_seconds(
            error,
            default_rate_limit,
        )

        mark_provider_blocked(
            name,
            seconds,
        )

        return

    if is_temporary_ai_error(error):
        mark_provider_blocked(
            name,
            default_temporary,
        )


# =========================================================
# OPENROUTER MODEL COOLDOWN
# =========================================================

def is_openrouter_model_available(model):
    now = time.time()

    with openrouter_model_lock:
        blocked_until = (
            openrouter_model_blocked_until.get(
                model,
                0,
            )
        )

    return now >= blocked_until


def mark_openrouter_model_blocked(
    model,
    seconds,
):
    until = (
        time.time()
        + max(1, int(seconds))
    )

    with openrouter_model_lock:
        openrouter_model_blocked_until[
            model
        ] = max(
            openrouter_model_blocked_until.get(
                model,
                0,
            ),
            until,
        )

    print(
        f"OPENROUTER MODEL BLOCK | "
        f"{model} | "
        f"{int(seconds)} sec",
        flush=True,
    )


def cleanup_openrouter_model_cooldowns():
    now = time.time()

    with openrouter_model_lock:
        for model in list(
            openrouter_model_blocked_until
        ):
            if (
                openrouter_model_blocked_until[
                    model
                ] <= now
            ):
                openrouter_model_blocked_until.pop(
                    model,
                    None,
                )


# =========================================================
# LOCAL MODE HELPERS
# =========================================================

SIMPLE_LOCAL_MESSAGES = {
    "привет",
    "прив",
    "здарова",
    "здравствуйте",
    "добрый день",
    "доброе утро",
    "добрый вечер",
    "хай",
    "hello",
    "hi",
    "йо",
    "ку",
    "ок",
    "окей",
    "оке",
    "ага",
    "угу",
    "да",
    "нет",
    "понятно",
    "ясно",
    "спасибо",
    "спс",
    "пасиб",
    "благодарю",
    "понял",
    "поняла",
    "хорошо",
    "ладно",
    "норм",
    "нормально",
    "го",
    "погнали",
}


def normalize_simple_message(text):
    text = (text or "").lower().strip()

    text = re.sub(
        r"[!?.,:;]+",
        "",
        text,
    )

    text = re.sub(
        r"\s+",
        " ",
        text,
    )

    return text.strip()


def is_simple_local_message(text):
    normalized = normalize_simple_message(
        text
    )

    if normalized in SIMPLE_LOCAL_MESSAGES:
        return True

    if len(normalized) <= 3:
        return True

    return False


def local_mood_prefix(state):
    if not state:
        return ""

    mood = state.get(
        "mood",
        "normal",
    )

    if mood == "very_offended":
        return random.choice([
            "Я всё ещё обижен 😒",
            "Я пока ещё дуюсь 😒",
            "Настроение всё ещё так себе 😒",
        ])

    if mood == "offended":
        return random.choice([
            "Я ещё немного обижен 😒",
            "Ну я всё ещё помню, между прочим 😒",
            "Ладно, но осадочек остался 😂",
        ])

    if mood == "angry":
        return random.choice([
            "Да блин 😒",
            "Ну ё-моё 😂",
            "Чё опять? 😒",
        ])

    if mood == "annoyed":
        return random.choice([
            "Ну давай 😂",
            "Я тебя слушаю 😒",
            "Ага, вижу тебя.",
        ])

    if mood == "friendly":
        return random.choice([
            "😎",
            "Ахах, ну давай.",
            "Я тут 😂",
        ])

    return ""


def get_recent_local_context(
    chat_id,
    limit=5,
):
    history = get_chat_memory(
        chat_id,
        limit,
    )

    return history or []


def local_memory_text(
    chat_id,
    user_id,
):
    personal = get_user_memory(
        chat_id,
        user_id,
    )

    if not personal:
        return None

    memory = (
        personal.get("memory")
        or ""
    ).strip()

    if not memory:
        return None

    return memory


def looks_like_memory_question(text):
    low = (
        text or ""
    ).lower()

    patterns = (
        "что ты помнишь",
        "что помнишь",
        "что ты обо мне знаешь",
        "что обо мне знаешь",
        "что ты знаешь обо мне",
        "помнишь меня",
        "ты меня помнишь",
        "какая у меня память",
        "моя память",
    )

    return any(
        pattern in low
        for pattern in patterns
    )


def looks_like_tank_question(text):
    low = (
        text or ""
    ).lower()

    return any(
        word in low
        for word in (
            "танк",
            "танке",
            "танка",
            "танков",
            "блиц",
            "tanks blitz",
        )
    )


def local_answer(
    chat_id,
    text,
    user_id=None,
    user_name=None,
):
    """
    Полностью локальный ответ.

    Здесь НЕТ вызовов Groq/OpenRouter.

    Используются:
    - эмоции;
    - личная память;
    - память чата;
    - знания;
    - локальная база Tanks Blitz;
    - простые шаблоны.
    """

    text = normalize_text(text)

    state = EMOTION_DEFAULT.copy()

    if user_id is not None:
        try:
            state = get_emotion_state(
                chat_id,
                int(user_id),
            )
        except Exception:
            state = EMOTION_DEFAULT.copy()

    low = text.lower()
    normalized = normalize_simple_message(
        text
    )

    # -----------------------------------------------------
    # Простые сообщения
    # -----------------------------------------------------

    if normalized in (
        "привет",
        "прив",
        "здарова",
        "здравствуйте",
        "добрый день",
        "доброе утро",
        "добрый вечер",
        "хай",
        "hello",
        "hi",
        "йо",
        "ку",
    ):
        if state.get("mood") in (
            "very_offended",
            "offended",
        ):
            return random.choice([
                "Привет... 😒",
                "Ага, привет.",
                "Привет. Я ещё немного обижен 😂",
            ])

        return random.choice([
            "Привет 😎",
            "О, привет.",
            "Йо 👋",
            "Привет 😂",
            "Здарова.",
            "Я тут.",
        ])

    if normalized in (
        "спасибо",
        "спс",
        "пасиб",
        "благодарю",
    ):
        return random.choice([
            "Да не за что 😎",
            "Пожалуйста.",
            "Всегда пожалуйста 😂",
            "Да ладно.",
        ])

    if normalized in (
        "ок",
        "окей",
        "оке",
        "ага",
        "угу",
        "понятно",
        "ясно",
        "понял",
        "поняла",
        "хорошо",
        "ладно",
        "норм",
        "нормально",
    ):
        return random.choice([
            "👍",
            "Ага.",
            "Окей.",
            "Понял.",
            "Ну и отлично 😎",
            "Договорились.",
        ])

    if normalized in (
        "да",
        "нет",
    ):
        return random.choice([
            "Ага.",
            "Понял.",
            "Окей.",
            "Принял.",
        ])

    # -----------------------------------------------------
    # Память пользователя
    # -----------------------------------------------------

    if looks_like_memory_question(
        text
    ):
        memory = local_memory_text(
            chat_id,
            user_id,
        )

        if memory:
            return (
                "Вот что я помню о тебе:\n"
                + memory[:1800]
            )

        return random.choice([
            "Пока ничего полезного о тебе не накопил 😅",
            "Пока в памяти о тебе пустовато.",
            "Ничего существенного пока не запомнил.",
        ])

    # -----------------------------------------------------
    # Явное сохранение памяти
    # -----------------------------------------------------

    if re.search(
        r"\bзапомни\b",
        low,
        re.IGNORECASE,
    ):
        if user_id is not None:
            saved = save_explicit_user_memory(
                chat_id,
                int(user_id),
                user_name,
                text,
            )

            if saved:
                return random.choice([
                    "Запомнил 👍",
                    "Ага, сохранил.",
                    "Всё, запомнил 😎",
                    "Есть, это уже в памяти.",
                ])

        return "Ага, понял."

    # -----------------------------------------------------
    # Локальная база Tanks Blitz
    # -----------------------------------------------------

    if looks_like_tank_question(
        text
    ):
        tanks = get_tank_knowledge_for_text(
            text,
            limit=5,
        )

        if tanks:
            lines = []

            for tank in tanks:
                name = (
                    tank.get("name")
                    or ""
                ).strip()

                nation = (
                    tank.get("nation")
                    or ""
                ).strip()

                tier = (
                    tank.get("tier")
                    or ""
                )

                if name:
                    line = f"• {name}"

                    if nation:
                        line += (
                            f" — {nation}"
                        )

                    if tier:
                        line += (
                            f", уровень {tier}"
                        )

                    lines.append(line)

            if lines:
                return (
                    "Из того, что есть у меня "
                    "в локальной базе:\n"
                    + "\n".join(lines)
                )

    # -----------------------------------------------------
    # Эмоциональная реакция
    # -----------------------------------------------------

    if detect_emotion_action(
        text
    ) == "insult":
        short = get_emotion_short_reaction(
            state,
            text,
        )

        if short:
            return short

        return random.choice([
            "Эй, полегче 😒",
            "Ну ты чего 😂",
            "Вот это ты загнул.",
            "Не начинай.",
        ])

    if detect_emotion_action(
        text
    ) == "apology":
        return random.choice([
            "Ладно, проехали 😌",
            "Да всё, мир.",
            "Ладно, не держу зла.",
            "Окей, забыли.",
        ])

    if detect_emotion_action(
        text
    ) == "praise":
        return random.choice([
            "Ахах, спасибо 😎",
            "Вот это уже приятно.",
            "Красиво сказал 😂",
            "Ну всё, засмущал.",
        ])

    # -----------------------------------------------------
    # Локальная работа с последним контекстом
    # -----------------------------------------------------

    recent = get_recent_local_context(
        chat_id,
        5,
    )

    recent_user_messages = [
        (
            item.get("content")
            or ""
        ).strip()
        for item in recent
        if item.get("role") == "user"
        and (
            item.get("content")
            or ""
        ).strip()
    ]

    # -----------------------------------------------------
    # Короткие вопросы
    # -----------------------------------------------------

    if looks_like_question(text):

        # Если есть релевантное сохранённое знание,
        # используем его вместо пустого ответа.
        knowledge = get_knowledge(
            chat_id
        )

        if knowledge:
            for item in knowledge:
                value = (
                    item.get("knowledge")
                    or ""
                ).strip()

                if not value:
                    continue

                tokens = [
                    token
                    for token in re.findall(
                        r"[a-zа-яё0-9]+",
                        low,
                    )
                    if len(token) >= 4
                ]

                value_low = value.lower()

                if any(
                    token in value_low
                    for token in tokens[:6]
                ):
                    return value[:1800]

        # Если вопрос относится к предыдущей реплике,
        # локально поддерживаем разговор.
        if recent_user_messages:
            return random.choice([
                "Понял тебя. Расскажи чуть подробнее.",
                "Хм, понял вопрос.",
                "Интересный вопрос. Давай разберёмся.",
                "Ага, понял, о чём ты.",
                "Слушаю тебя.",
            ])

        return random.choice([
            "Хм. А можешь чуть подробнее?",
            "Понял вопрос. Расскажи контекст.",
            "Интересно. Уточни немного.",
            "Слушаю 👀",
        ])

    # -----------------------------------------------------
    # Обычная реплика
    # -----------------------------------------------------

    if len(text.split()) <= 3:
        return random.choice([
            "Ага 😎",
            "Понял.",
            "Угу.",
            "Да-да.",
            "Я тебя слушаю.",
            "Хех 😂",
        ])

    if len(text.split()) <= 8:
        return random.choice([
            "Ага, понял тебя.",
            "Да, есть такое.",
            "Хм, звучит интересно.",
            "Понял, о чём ты.",
            "Ахах, бывает 😂",
            "Угу, продолжаем.",
        ])

    # Более длинная реплика.
    # Здесь специально не говорится пользователю
    # про AI или LOCAL MODE.
    return random.choice([
        "Понял тебя. Продолжай.",
        "Ага, уловил мысль.",
        "Да, контекст понял.",
        "Хм, интересно. Я тебя слушаю.",
        "Понял. Давай дальше.",
        "Ага, нормально.",
    ])


# =========================================================
# SIMPLE MESSAGE AI FILTER
# =========================================================

def should_use_ai_for_message(text):
    """
    Экономия токенов.

    Простые реплики не требуют AI даже тогда,
    когда AI полностью доступен.
    """

    normalized = normalize_simple_message(
        text
    )

    if not normalized:
        return False

    if is_simple_local_message(
        normalized
    ):
        return False

    # Очень короткие эмоциональные реплики.
    if len(normalized) <= 4:
        return False

    return True


# =========================================================
# EMOTION DB
# =========================================================

def get_emotion_state(
    chat_id,
    user_id,
):
    if user_id is None:
        return EMOTION_DEFAULT.copy()

    try:
        result = (
            supabase
            .table("bot_users")
            .select(
                "mood, offense, "
                "irritation, trust"
            )
            .eq(
                "chat_id",
                db_chat_id(chat_id),
            )
            .eq(
                "user_id",
                db_user_id(user_id),
            )
            .limit(1)
            .execute()
        )

        if not result.data:
            return EMOTION_DEFAULT.copy()

        row = result.data[0]

        return {
            "mood": row.get("mood") or "normal",
            "offense": int(
                row.get("offense", 0) or 0
            ),
            "irritation": int(
                row.get("irritation", 0) or 0
            ),
            "trust": int(
                row.get("trust", 50) or 50
            ),
        }

    except Exception as e:
        print(
            "Emotion load error:",
            e,
            flush=True,
        )

        return EMOTION_DEFAULT.copy()


def save_emotion_state(
    chat_id,
    user_id,
    state,
):
    if user_id is None:
        return

    try:
        data = {
            "chat_id": db_chat_id(chat_id),
            "user_id": db_user_id(user_id),
            "mood": state.get(
                "mood",
                "normal",
            ),
            "offense": int(
                state.get("offense", 0)
            ),
            "irritation": int(
                state.get("irritation", 0)
            ),
            "trust": int(
                state.get("trust", 50)
            ),
            "updated_at": utc_now(),
            "last_emotion_update": utc_now(),
        }

        existing = (
            supabase
            .table("bot_users")
            .select("id")
            .eq(
                "chat_id",
                db_chat_id(chat_id),
            )
            .eq(
                "user_id",
                db_user_id(user_id),
            )
            .limit(1)
            .execute()
        )

        if existing.data:
            (
                supabase
                .table("bot_users")
                .update(data)
                .eq(
                    "id",
                    existing.data[0]["id"],
                )
                .execute()
            )

        else:
            (
                supabase
                .table("bot_users")
                .insert(data)
                .execute()
            )

    except Exception as e:
        print(
            "Emotion save error:",
            e,
            flush=True,
        )


def process_emotion(
    chat_id,
    user_id,
    text,
):
    if user_id is None:
        return EMOTION_DEFAULT.copy()

    state = get_emotion_state(
        chat_id,
        user_id,
    )

    previous_mood = state.get(
        "mood",
        "normal",
    )

    state = update_emotion_state(
        state,
        text,
    )

    save_emotion_state(
        chat_id,
        user_id,
        state,
    )

    if previous_mood != state["mood"]:
        print(
            "EMOTION CHANGE | "
            f"user={user_id} | "
            f"{previous_mood} -> {state['mood']} | "
            f"offense={state['offense']} | "
            f"trust={state['trust']}",
            flush=True,
        )

    return state


# =========================================================
# EVENT PROTECTION
# =========================================================

def already_processed(event_id):
    if not event_id:
        return False

    with events_lock:
        now = time.time()

        for key in list(processed_events):
            if (
                now - processed_events[key]
                > EVENT_CACHE_TIME
            ):
                processed_events.pop(
                    key,
                    None,
                )

        if event_id in processed_events:
            return True

        processed_events[event_id] = now

        if len(processed_events) > EVENT_CACHE_LIMIT:
            oldest = min(
                processed_events,
                key=processed_events.get,
            )

            processed_events.pop(
                oldest,
                None,
            )

        return False


# =========================================================
# VK USER
# =========================================================

def get_vk_user_name(user_id):
    if not user_id:
        return None

    cached = user_names.get(
        str(user_id)
    )

    if (
        cached
        and time.time() - cached[0]
        < NAME_CACHE_TIME
    ):
        return cached[1]

    try:
        data = requests.get(
            f"{VK_API}/users.get",
            params={
                "access_token": VK_TOKEN,
                "v": VK_VERSION,
                "user_ids": user_id,
            },
            timeout=10,
        ).json()

        users = data.get(
            "response",
            [],
        )

        if not users:
            return None

        user = users[0]

        name = (
            f"{user.get('first_name', '').strip()} "
            f"{user.get('last_name', '').strip()}"
        ).strip()

        if name:
            user_names[
                str(user_id)
            ] = (
                time.time(),
                name,
            )

        return name or None

    except Exception as e:
        print(
            "VK name error:",
            e,
            flush=True,
        )
        return None


# =========================================================
# TELEGRAM USER
# =========================================================

def get_telegram_user_name(user):
    if not user:
        return None

    uid = str(
        user.get("id", "")
    )

    cached = tg_user_names.get(uid)

    if (
        cached
        and time.time() - cached[0]
        < NAME_CACHE_TIME
    ):
        return cached[1]

    name = (
        f"{user.get('first_name', '').strip()} "
        f"{user.get('last_name', '').strip()}"
    ).strip()

    if not name:
        name = (
            user.get(
                "username",
                "",
            ).strip()
        )

    if name:
        tg_user_names[uid] = (
            time.time(),
            name,
        )

    return name or None


# =========================================================
# CHAT MEMORY
# =========================================================

def save_chat_message(
    chat_id,
    speaker_id,
    speaker_name,
    role,
    content,
):
    if chat_id is None or not content:
        return

    try:
        database_speaker_id = None

        if speaker_id is not None:
            try:
                database_speaker_id = db_user_id(
                    speaker_id
                )
            except (
                ValueError,
                TypeError,
            ):
                database_speaker_id = None

        (
            supabase
            .table("bot_chat_memory")
            .insert({
                "chat_id": db_chat_id(chat_id),
                "speaker_id": database_speaker_id,
                "speaker_name": (
                    speaker_name or ""
                ),
                "role": role,
                "content": str(content)[:4000],
            })
            .execute()
        )

    except Exception as e:
        print(
            "Chat memory save error:",
            e,
            flush=True,
        )


def get_chat_memory(
    chat_id,
    limit=CHAT_MEMORY_LIMIT,
):
    try:
        result = (
            supabase
            .table("bot_chat_memory")
            .select(
                "speaker_id, "
                "speaker_name, "
                "role, "
                "content"
            )
            .eq(
                "chat_id",
                db_chat_id(chat_id),
            )
            .order(
                "created_at",
                desc=True,
            )
            .limit(limit)
            .execute()
        )

        rows = result.data or []

        rows.reverse()

        return rows

    except Exception as e:
        print(
            "Chat memory load error:",
            e,
            flush=True,
        )

        return []


# =========================================================
# OTHER BOT FEATURES
# =========================================================

NOT_MY_FEATURE_KEYWORDS = (
    "рейтинг",
    "топ активны",
    "топ-10",
    "топ 10",
    "нашивк",
    "мой статус",
    "какой у меня статус",
    "статистика",
    "активност",
    "активные",
    "общий список",
    "матч",
    "турнир",
)


MENU_BUTTON_PREFIXES = (
    "общ",
    "нашивк",
    "актив",
    "арен",
    "рейтинг",
    "топ",
    "статист",
    "статус",
    "матч",
    "турнир",
)


def looks_like_not_my_feature_question(
    text,
):
    low = (
        text or ""
    ).lower()

    if any(
        word in low
        for word in NOT_MY_FEATURE_KEYWORDS
    ):
        return True

    stripped = re.sub(
        r"[^a-zа-яё\s]",
        " ",
        low,
    ).strip()

    words = [
        w
        for w in stripped.split()
        if w
    ]

    if not words or len(words) > 3:
        return False

    return any(
        word.startswith(prefix)
        for word in words
        for prefix in MENU_BUTTON_PREFIXES
    )


def get_chat_message_count(chat_id):
    try:
        result = (
            supabase
            .table("bot_chat_memory")
            .select(
                "id",
                count="exact",
                head=True,
            )
            .eq(
                "chat_id",
                db_chat_id(chat_id),
            )
            .execute()
        )

        return int(
            result.count or 0
        )

    except Exception as e:
        print(
            "Chat message count error:",
            e,
            flush=True,
        )

        return 0


# =========================================================
# KNOWLEDGE
# =========================================================

def knowledge_fingerprint(
    chat_id,
    knowledge,
):
    raw = (
        str(chat_id).strip()
        + "|"
        + normalize_text(
            knowledge
        ).lower()
    )

    return hashlib.sha256(
        raw.encode("utf-8")
    ).hexdigest()


def save_knowledge(
    chat_id,
    knowledge,
    importance=1,
):
    knowledge = normalize_text(
        knowledge
    )

    if len(knowledge) < 5:
        return

    try:
        database_chat_id = db_chat_id(
            chat_id
        )

        fingerprint = knowledge_fingerprint(
            database_chat_id,
            knowledge,
        )

        existing = (
            supabase
            .table("bot_knowledge")
            .select("id")
            .eq(
                "chat_id",
                database_chat_id,
            )
            .eq(
                "fingerprint",
                fingerprint,
            )
            .limit(1)
            .execute()
        )

        if existing.data:
            return

        (
            supabase
            .table("bot_knowledge")
            .insert({
                "chat_id": database_chat_id,
                "knowledge": knowledge[:2000],
                "importance": max(
                    1,
                    min(int(importance), 5),
                ),
                "fingerprint": fingerprint,
            })
            .execute()
        )

        print(
            "NEW KNOWLEDGE:",
            knowledge[:150],
            flush=True,
        )

    except Exception as e:
        print(
            "Knowledge save error:",
            e,
            flush=True,
        )


def get_knowledge(chat_id):
    try:
        result = (
            supabase
            .table("bot_knowledge")
            .select(
                "knowledge, importance"
            )
            .eq(
                "chat_id",
                db_chat_id(chat_id),
            )
            .order(
                "importance",
                desc=True,
            )
            .order(
                "created_at",
                desc=True,
            )
            .limit(KNOWLEDGE_LIMIT)
            .execute()
        )

        return result.data or []

    except Exception as e:
        print(
            "Knowledge load error:",
            e,
            flush=True,
        )

        return []


# =========================================================
# USER MEMORY
# =========================================================

def merge_memory(
    old_memory,
    new_fact,
):
    facts = []

    if old_memory:
        facts.extend(
            line.strip("-• \t")
            for line in old_memory.splitlines()
            if line.strip()
        )

    new_fact = new_fact.strip(
        "-• \t"
    )

    if new_fact:
        facts.append(new_fact)

    result = []
    seen = set()

    for fact in facts:
        normalized = normalize_text(
            fact
        ).lower()

        if (
            normalized
            and normalized not in seen
        ):
            seen.add(normalized)
            result.append(fact)

    return "\n".join(
        result[-USER_MEMORY_LIMIT:]
    )


def save_user_memory(
    chat_id,
    user_id,
    name,
    memory,
):
    if (
        chat_id is None
        or user_id is None
        or not memory
    ):
        return

    memory = normalize_text(
        memory
    )

    if len(memory) < 5:
        return

    try:
        database_chat_id = db_chat_id(
            chat_id
        )

        database_user_id = db_user_id(
            user_id
        )

        existing = (
            supabase
            .table("bot_users")
            .select(
                "id, memory, name"
            )
            .eq(
                "chat_id",
                database_chat_id,
            )
            .eq(
                "user_id",
                database_user_id,
            )
            .limit(1)
            .execute()
        )

        old_memory = (
            existing.data[0].get(
                "memory",
                "",
            )
            if existing.data
            else ""
        )

        old_name = (
            existing.data[0].get(
                "name",
                "",
            )
            if existing.data
            else ""
        )

        final_name = (
            name
            or old_name
            or ""
        )

        final_memory = merge_memory(
            old_memory,
            memory,
        )[:3000]

        data = {
            "chat_id": database_chat_id,
            "user_id": database_user_id,
            "name": final_name,
            "memory": final_memory,
            "updated_at": utc_now(),
        }

        if existing.data:
            (
                supabase
                .table("bot_users")
                .update(data)
                .eq(
                    "id",
                    existing.data[0]["id"],
                )
                .execute()
            )

        else:
            (
                supabase
                .table("bot_users")
                .insert(data)
                .execute()
            )

        print(
            f"USER MEMORY "
            f"[{final_name or user_id}]: "
            f"{memory[:150]}",
            flush=True,
        )

    except Exception as e:
        print(
            "User memory save error:",
            e,
            flush=True,
        )


def save_explicit_user_memory(
    chat_id,
    user_id,
    user_name,
    text,
):
    if (
        chat_id is None
        or user_id is None
        or not text
    ):
        return False

    original = text.strip()

    if not original:
        return False

    fact = None

    match = re.search(
        r"(?:запомни|запомни\s+это|"
        r"запомни\s+пожалуйста)"
        r"\s*[:,-]?\s*(?:что\s+)?(.+)$",
        original,
        re.IGNORECASE,
    )

    if match:
        statement = (
            match.group(1) or ""
        ).strip()

        if statement:
            tank_match = re.search(
                r"мой\s+любим(?:ый|ая|ое|ые)"
                r"\s+танк(?:а|ов)?"
                r"\s*(?:—|-|:|=|это|есть)?"
                r"\s*(.+)$",
                statement,
                re.IGNORECASE,
            )

            if tank_match:
                tank = (
                    tank_match.group(1)
                    or ""
                ).strip(
                    " .,!?;"
                )

                if tank:
                    fact = (
                        "Любимый танк — "
                        + tank
                    )

            if (
                fact is None
                and len(statement) <= 500
            ):
                fact = statement

    if fact is None:
        tank_match = re.search(
            r"мой\s+любим(?:ый|ая|ое|ые)"
            r"\s+танк(?:а|ов)?"
            r"\s*(?:—|-|:|=|это|есть)?"
            r"\s*(.+)$",
            original,
            re.IGNORECASE,
        )

        if tank_match:
            tank = (
                tank_match.group(1)
                or ""
            ).strip(
                " .,!?;"
            )

            if tank:
                fact = (
                    "Любимый танк — "
                    + tank
                )

    if not fact:
        return False

    sensitive_words = (
        "пароль",
        "password",
        "номер карты",
        "банковская карта",
        "cvv",
        "cvc",
        "паспорт",
        "документ",
        "адрес проживания",
    )

    if any(
        word in fact.lower()
        for word in sensitive_words
    ):
        return False

    save_user_memory(
        chat_id,
        user_id,
        user_name,
        fact,
    )

    print(
        f"EXPLICIT MEMORY SAVED | "
        f"chat={chat_id} | "
        f"user={user_id} | "
        f"{fact}",
        flush=True,
    )

    return True


def get_user_memory(
    chat_id,
    user_id,
):
    if user_id is None:
        return None

    try:
        result = (
            supabase
            .table("bot_users")
            .select(
                "name, memory, updated_at"
            )
            .eq(
                "chat_id",
                db_chat_id(chat_id),
            )
            .eq(
                "user_id",
                db_user_id(user_id),
            )
            .limit(1)
            .execute()
        )

        if not result.data:
            return None

        return result.data[0]

    except Exception as e:
        print(
            "User memory load error:",
            e,
            flush=True,
        )

        return None


# =========================================================
# LEARNING STATE
# =========================================================

def get_learning_state(chat_id):
    try:
        database_chat_id = db_chat_id(
            chat_id
        )

        result = (
            supabase
            .table("bot_learning_state")
            .select("*")
            .eq(
                "chat_id",
                database_chat_id,
            )
            .limit(1)
            .execute()
        )

        if result.data:
            return result.data[0]

        (
            supabase
            .table("bot_learning_state")
            .insert({
                "chat_id": database_chat_id,
                "messages_since_learning": 0,
                "development_stage": 1,
                "personality": "",
                "last_learning_at": utc_now(),
            })
            .execute()
        )

        return {
            "chat_id": database_chat_id,
            "messages_since_learning": 0,
            "development_stage": 1,
            "personality": "",
            "last_learning_at": utc_now(),
        }

    except Exception as e:
        print(
            "Learning state error:",
            e,
            flush=True,
        )

        return {
            "chat_id": db_chat_id(chat_id),
            "messages_since_learning": 0,
            "development_stage": 1,
            "personality": "",
        }


def increase_learning_counter(chat_id):
    state = get_learning_state(
        chat_id
    )

    previous = int(
        state.get(
            "messages_since_learning",
            0,
        )
        or 0
    )

    count = min(
        previous + 1,
        LEARNING_EVERY_MESSAGES,
    )

    for attempt in range(3):
        try:
            (
                supabase
                .table("bot_learning_state")
                .update({
                    "messages_since_learning": count
                })
                .eq(
                    "chat_id",
                    db_chat_id(chat_id),
                )
                .execute()
            )

            return count

        except Exception as e:
            text = str(e).lower()

            temporary = (
                "resource temporarily unavailable"
                in text
                or "connection"
                in text
                or "timeout"
                in text
            )

            if not temporary or attempt == 2:
                print(
                    "Learning counter error:",
                    e,
                    flush=True,
                )
                return count

            time.sleep(
                0.4 * (attempt + 1)
            )

    return count


def reset_learning_counter(chat_id):
    try:
        (
            supabase
            .table("bot_learning_state")
            .update({
                "messages_since_learning": 0
            })
            .eq(
                "chat_id",
                db_chat_id(chat_id),
            )
            .execute()
        )

    except Exception as e:
        print(
            "Learning counter reset error:",
            e,
            flush=True,
        )


# =========================================================
# TEXT CLEANER
# =========================================================

def clean_model_text(text):
    if not text:
        return ""

    text = str(text)

    text = re.sub(
        r"<think>.*?</think>",
        "",
        text,
        flags=re.DOTALL | re.IGNORECASE,
    )

    for tag in (
        "think",
        "analysis",
        "reasoning",
    ):
        text = re.sub(
            rf"<{tag}>.*?</{tag}>",
            "",
            text,
            flags=re.DOTALL | re.IGNORECASE,
        )

        text = re.sub(
            rf"<{tag}>.*$",
            "",
            text,
            flags=re.DOTALL | re.IGNORECASE,
        )

    text = re.sub(
        r"^\s*(?:assistant|final)\s*:\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )

    return text.strip()


LEAKED_REASONING_MARKERS = (
    "here's a thinking process",
    "here is a thinking process",
    "let me think",
    "let me analyze",
    "the user is asking",
    "the current speaker is",
    "i need to check",
    "looking at the personal memory",
    "identify key elements",
    "analyze user input",
    "chain of thought",
)


def looks_like_leaked_reasoning(text):
    if not text:
        return False

    low = text.lower()

    if any(
        marker in low
        for marker in LEAKED_REASONING_MARKERS
    ):
        return True

    numbered_steps = re.findall(
        r"(?:^|\n)\s*\d+\.\s*\*\*",
        text,
    )

    if len(numbered_steps) >= 2:
        return True

    return False


# =========================================================
# GROQ REQUEST
# =========================================================

def ask_model(
    model,
    messages,
    max_tokens=GROQ_MAX_TOKENS,
):
    if groq is None:
        raise RuntimeError(
            "GROQ_API_KEY не установлен."
        )

    completion = None
    first_error = None

    try:
        completion = (
            groq.chat.completions.create(
                model=model,
                messages=messages,
                max_completion_tokens=max_tokens,
                reasoning_effort="low",
                include_reasoning=False,
            )
        )

    except Exception as e:
        first_error = e

        print(
            "Groq request failed:",
            e,
            flush=True,
        )

        if is_rate_limit_error(e):
            raise

        try:
            completion = (
                groq.chat.completions.create(
                    model=model,
                    messages=messages,
                    max_tokens=max_tokens,
                    reasoning_effort="low",
                    include_reasoning=False,
                )
            )

        except Exception:
            raise first_error

    usage = getattr(
        completion,
        "usage",
        None,
    )

    if usage:
        print(
            "Groq:",
            "prompt=",
            getattr(
                usage,
                "prompt_tokens",
                None,
            ),
            "completion=",
            getattr(
                usage,
                "completion_tokens",
                None,
            ),
            "total=",
            getattr(
                usage,
                "total_tokens",
                None,
            ),
            flush=True,
        )

    choices = getattr(
        completion,
        "choices",
        None,
    ) or []

    if not choices:
        raise RuntimeError(
            "Groq returned no choices."
        )

    message = getattr(
        choices[0],
        "message",
        None,
    )

    if message is None:
        raise RuntimeError(
            "Groq returned empty message."
        )

    reply = clean_model_text(
        getattr(
            message,
            "content",
            None,
        )
        or ""
    )

    if reply and looks_like_leaked_reasoning(
        reply
    ):
        raise RuntimeError(
            "Groq returned raw reasoning."
        )

    if reply:
        return reply

    raise RuntimeError(
        "Groq returned empty final response."
    )


# =========================================================
# OPENROUTER PARSER
# =========================================================

def extract_openrouter_text(data):
    choices = data.get(
        "choices"
    ) or []

    if not choices:
        return ""

    message = (
        choices[0].get(
            "message"
        )
        or {}
    )

    content = message.get(
        "content"
    )

    if isinstance(
        content,
        str,
    ):
        return clean_model_text(
            content
        )

    if isinstance(
        content,
        list,
    ):
        parts = []

        for part in content:

            if isinstance(
                part,
                str,
            ):
                parts.append(part)
                continue

            if not isinstance(
                part,
                dict,
            ):
                continue

            if part.get(
                "type"
            ) in (
                "text",
                "output_text",
            ):
                value = (
                    part.get("text")
                    or part.get("content")
                    or ""
                )

                if value:
                    parts.append(
                        str(value)
                    )

        return clean_model_text(
            "\n".join(parts)
        )

    choice_text = choices[0].get(
        "text"
    )

    if (
        isinstance(
            choice_text,
            str,
        )
        and choice_text.strip()
    ):
        return clean_model_text(
            choice_text
        )

    return ""


# =========================================================
# OPENROUTER MODEL DISCOVERY / CLASSIFICATION
# =========================================================

def openrouter_error_status(error):
    match = re.search(r"HTTP\s+(\d{3})", str(error), re.IGNORECASE)
    return int(match.group(1)) if match else None


def is_openrouter_not_found_error(error):
    text = str(error).lower()
    status = openrouter_error_status(error)
    return status in (400, 404) and any(
        marker in text
        for marker in (
            "model not found",
            "not found",
            "unavailable",
            "free version is no longer available",
            "free version unavailable",
        )
    )


def is_openrouter_daily_limit_error(error):
    text = str(error).lower()
    return (
        "free-models-per-day" in text
        or "free models per day" in text
        or "free-models" in text and "per day" in text
    )


def mark_openrouter_model_unavailable(model, seconds=None, reason="unavailable"):
    if seconds is None:
        seconds = OPENROUTER_MODEL_UNAVAILABLE_BLOCK

    mark_openrouter_model_blocked(model, seconds)

    print(
        f"OPENROUTER MODEL UNAVAILABLE | {model} | "
        f"{reason} | {int(seconds)} sec",
        flush=True,
    )


def discover_openrouter_free_models(force=False):
    """
    Получает актуальный список моделей OpenRouter и оставляет
    бесплатные модели. Это запасной механизм: основной fallback
    остаётся openrouter/free.
    """
    global openrouter_discovered_models
    global openrouter_discovered_at

    if not OPENROUTER_API_KEY:
        return []

    now = time.time()

    with openrouter_discovery_lock:
        if (
            not force
            and openrouter_discovered_models
            and now - openrouter_discovered_at < OPENROUTER_DISCOVERY_CACHE_TIME
        ):
            return list(openrouter_discovered_models)

    try:
        response = requests.get(
            OPENROUTER_MODELS_API,
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            },
            timeout=OPENROUTER_DISCOVERY_TIMEOUT,
        )

        if response.status_code != 200:
            print(
                "OpenRouter model discovery failed:",
                response.status_code,
                response.text[:500],
                flush=True,
            )
            return []

        data = response.json()
        rows = data.get("data") or []
        found = []

        for row in rows:
            if not isinstance(row, dict):
                continue

            model_id = str(row.get("id") or "").strip()
            if not model_id:
                continue

            # OpenRouter обычно помечает бесплатную модель suffix'ом :free.
            # Также учитываем pricing=0 на случай изменения формата.
            pricing = row.get("pricing") or {}
            prompt_price = str(pricing.get("prompt", ""))
            completion_price = str(pricing.get("completion", ""))
            is_free = (
                model_id.endswith(":free")
                or (prompt_price in ("0", "0.0", "0.000000")
                    and completion_price in ("0", "0.0", "0.000000"))
            )

            if not is_free:
                continue

            if model_id in KNOWN_DEAD_OPENROUTER_MODELS:
                continue

            if model_id not in found:
                found.append(model_id)

        with openrouter_discovery_lock:
            openrouter_discovered_models = found[:30]
            openrouter_discovered_at = time.time()

        print(
            f"OpenRouter discovery: found {len(found)} free models",
            flush=True,
        )

        return list(found[:30])

    except Exception as e:
        print(
            "OpenRouter model discovery error:",
            e,
            flush=True,
        )
        return []


def get_openrouter_candidate_models():
    cleanup_openrouter_model_cooldowns()

    candidates = []

    # Сначала пользовательская конфигурация.
    for model in OPENROUTER_MODELS:
        if model in KNOWN_DEAD_OPENROUTER_MODELS:
            continue
        if model not in candidates:
            candidates.append(model)

    # Потом актуальные free-модели из каталога.
    # Не добавляем их, если провайдер уже заблокирован из-за дневного лимита.
    if provider_available("openrouter"):
        for model in discover_openrouter_free_models():
            if model not in candidates:
                candidates.append(model)

    return [
        model
        for model in candidates
        if is_openrouter_model_available(model)
    ]


# =========================================================
# OPENROUTER SINGLE MODEL
# =========================================================

def ask_openrouter_messages(
    messages,
    max_tokens=OPENROUTER_MAX_TOKENS,
    label="OpenRouter",
    model=None,
):
    if not OPENROUTER_API_KEY:
        raise RuntimeError(
            "OPENROUTER_API_KEY не установлен."
        )

    selected_model = (
        model
        or OPENROUTER_MODELS[0]
    )

    payload = {
        "model": selected_model,
        "messages": messages,
        "max_tokens": max_tokens,
        "stream": False,
        "reasoning": {
            "exclude": True,
        },
    }

    try:
        response = requests.post(
            OPENROUTER_API,
            headers={
                "Authorization":
                    f"Bearer {OPENROUTER_API_KEY}",

                "Content-Type":
                    "application/json",

                "HTTP-Referer":
                    os.environ.get(
                        "OPENROUTER_REFERER",
                        "https://vk-bot-1-khev.onrender.com",
                    ),

                "X-Title":
                    os.environ.get(
                        "OPENROUTER_TITLE",
                        "Tanks Blitz AI",
                    ),
            },
            json=payload,
            timeout=60,
        )

    except requests.RequestException as e:
        raise RuntimeError(
            f"{label} network error: {e}"
        )

    if response.status_code != 200:
        body = response.text[:1500]

        raise RuntimeError(
            f"{label} HTTP "
            f"{response.status_code}: "
            f"{body}"
        )

    try:
        data = response.json()

    except ValueError:
        raise RuntimeError(
            f"{label} returned invalid JSON: "
            f"{response.text[:1000]}"
        )

    if data.get("error"):
        raise RuntimeError(
            f"{label} API error: "
            f"{data.get('error')}"
        )

    reply = extract_openrouter_text(
        data
    )

    if not reply:
        raise RuntimeError(
            f"{label} returned empty final response."
        )

    if looks_like_leaked_reasoning(
        reply
    ):
        raise RuntimeError(
            f"{label} returned raw reasoning."
        )

    return reply


# =========================================================
# OPENROUTER MULTI MODEL
# =========================================================

def ask_openrouter(
    chat_id,
    text,
    user_id,
    user_name,
):
    messages = build_chat_context(
        chat_id,
        user_id,
        user_name,
        text,
    )

    if not OPENROUTER_MODELS:
        raise RuntimeError(
            "Нет моделей OpenRouter."
        )

    last_error = None
    attempted = False

    candidate_models = get_openrouter_candidate_models()

    for model in candidate_models:

        if not is_openrouter_model_available(
            model
        ):
            print(
                f"OpenRouter -> {model} "
                f"SKIPPED: model cooldown",
                flush=True,
            )
            continue

        attempted = True

        try:
            print(
                f"OpenRouter -> {model}",
                flush=True,
            )

            return ask_openrouter_messages(
                messages,
                OPENROUTER_MAX_TOKENS,
                f"OpenRouter/{model}",
                model=model,
            )

        except Exception as e:
            last_error = e

            print(
                f"OpenRouter model error "
                f"[{model}]: {e}",
                flush=True,
            )

            if is_openrouter_daily_limit_error(e):
                # Это лимит всего free-каталога, а не одной модели.
                # Не долбим OpenRouter каждым новым сообщением.
                mark_provider_blocked(
                    "openrouter",
                    OPENROUTER_DAILY_LIMIT_BLOCK,
                )
                break

            if is_openrouter_not_found_error(e):
                # 404 не является временным rate-limit. Модель исключаем
                # надолго, чтобы fallback не тратил запросы на мёртвый slug.
                mark_openrouter_model_unavailable(
                    model,
                    OPENROUTER_MODEL_UNAVAILABLE_BLOCK,
                    "HTTP 404/invalid model",
                )

            elif is_rate_limit_error(e):
                seconds = get_retry_seconds(
                    e,
                    DEFAULT_OPENROUTER_BLOCK,
                )

                mark_openrouter_model_blocked(
                    model,
                    seconds,
                )

                # Если это дневной лимит free-моделей, блокируем весь OR.
                if is_openrouter_daily_limit_error(e):
                    mark_provider_blocked(
                        "openrouter",
                        OPENROUTER_DAILY_LIMIT_BLOCK,
                    )
                    break

            elif is_temporary_ai_error(e):
                mark_openrouter_model_blocked(
                    model,
                    TEMP_OPENROUTER_BLOCK,
                )

            continue

    if not attempted:
        raise RuntimeError(
            "Все модели OpenRouter "
            "находятся в cooldown."
        )

    raise RuntimeError(
        "Все модели OpenRouter "
        "не дали финальный ответ. "
        f"Последняя ошибка: {last_error}"
    )


# =========================================================
# LEARNING MODEL
# =========================================================

def learning_ai_available():
    return (
        provider_available("learning_backup")
        or provider_available("learning_main")
        or provider_available("openrouter")
    )


def ask_learning_model(messages):
    # Если вообще нет AI — обучение не запускаем.
    if not learning_ai_available():
        raise RuntimeError(
            "Learning skipped: "
            "all AI providers unavailable."
        )

    # Сначала backup 20B.
    if provider_available(
        "learning_backup"
    ):
        try:
            print(
                "Learning -> Groq 20B",
                flush=True,
            )

            return ask_model(
                BACKUP_MODEL,
                messages,
                LEARNING_MAX_TOKENS,
            )

        except Exception as e:

            mark_provider_error(
                "learning_backup",
                e,
                DEFAULT_GROQ_BACKUP_BLOCK,
                TEMP_GROQ_BACKUP_BLOCK,
            )

            print(
                "Learning 20B error:",
                e,
                flush=True,
            )

    # Потом 120B.
    if provider_available(
        "learning_main"
    ):
        try:
            print(
                "Learning -> Groq 120B",
                flush=True,
            )

            return ask_model(
                MAIN_MODEL,
                messages,
                LEARNING_MAX_TOKENS,
            )

        except Exception as e:

            mark_provider_error(
                "learning_main",
                e,
                DEFAULT_GROQ_MAIN_BLOCK,
                TEMP_GROQ_MAIN_BLOCK,
            )

            print(
                "Learning 120B error:",
                e,
                flush=True,
            )

    # OpenRouter.
    if (
        OPENROUTER_API_KEY
        and provider_available(
            "openrouter"
        )
    ):
        last_error = None

        for model in get_openrouter_candidate_models():

            if not is_openrouter_model_available(
                model
            ):
                continue

            try:
                print(
                    f"Learning -> OpenRouter "
                    f"{model}",
                    flush=True,
                )

                return ask_openrouter_messages(
                    messages,
                    LEARNING_MAX_TOKENS,
                    f"OpenRouter Learning/{model}",
                    model=model,
                )

            except Exception as e:
                last_error = e

                print(
                    f"OpenRouter learning error "
                    f"[{model}]: {e}",
                    flush=True,
                )

                if is_openrouter_daily_limit_error(e):
                    mark_provider_blocked(
                        "openrouter",
                        OPENROUTER_DAILY_LIMIT_BLOCK,
                    )
                    break

                if is_openrouter_not_found_error(e):
                    mark_openrouter_model_unavailable(
                        model,
                        OPENROUTER_MODEL_UNAVAILABLE_BLOCK,
                        "HTTP 404/invalid model",
                    )

                elif is_rate_limit_error(e):
                    seconds = get_retry_seconds(
                        e,
                        DEFAULT_OPENROUTER_BLOCK,
                    )

                    mark_openrouter_model_blocked(
                        model,
                        seconds,
                    )

                elif is_temporary_ai_error(e):
                    mark_openrouter_model_blocked(
                        model,
                        TEMP_OPENROUTER_BLOCK,
                    )

        if last_error and is_openrouter_daily_limit_error(last_error):
            mark_provider_blocked(
                "openrouter",
                OPENROUTER_DAILY_LIMIT_BLOCK,
            )

        elif (
            last_error
            and is_rate_limit_error(
                last_error
            )
        ):
            mark_provider_blocked(
                "openrouter",
                get_retry_seconds(
                    last_error,
                    DEFAULT_OPENROUTER_BLOCK,
                ),
            )

        elif (
            last_error
            and is_temporary_ai_error(
                last_error
            )
        ):
            mark_provider_blocked(
                "openrouter",
                TEMP_OPENROUTER_BLOCK,
            )

    update_local_mode()

    raise RuntimeError(
        "Все модели временно "
        "недоступны для обучения."
    )


# =========================================================
# SELF LEARNING
# =========================================================

def perform_learning(chat_id):
    try:
        # ВАЖНО:
        # Если LOCAL MODE активен, здесь вообще
        # не отправляем историю пользователей в AI.
        if is_local_mode():
            print(
                "🧠 Learning skipped: LOCAL MODE",
                flush=True,
            )
            return

        if not learning_ai_available():
            update_local_mode()

            print(
                "🧠 Learning skipped: "
                "no AI available",
                flush=True,
            )
            return

        state = get_learning_state(
            chat_id
        )

        history = get_chat_memory(
            chat_id,
            LEARNING_HISTORY_LIMIT,
        )

        if len(history) < 10:
            reset_learning_counter(
                chat_id
            )
            return

        text_parts = []
        known_names = {}

        for item in history:

            if item.get("role") != "user":
                continue

            name = (
                item.get(
                    "speaker_name"
                )
                or "Участник"
            )

            uid = str(
                item.get(
                    "speaker_id"
                )
                or ""
            )

            if (
                uid
                and name != "Участник"
            ):
                known_names[uid] = name

            content = (
                item.get(
                    "content"
                )
                or ""
            )

            if content:
                text_parts.append(
                    f"[ID:{uid}] "
                    f"{name}: "
                    f"{content}"
                )

        if not text_parts:
            return

        prompt = f"""
Ты — модуль долговременного обучения
AI-участника конкретного чата.

Анализируй только реальные сообщения участников.

Ищи:

- явные факты об участниках;
- устойчивые интересы;
- предпочтения;
- любимые танки;
- устойчивые привычки общения;
- локальные шутки;
- важные события;
- полезный игровой контекст;
- правила и особенности чата.

Формат:

USER|ID|Факт

или:

CHAT|Факт|важность

Важность: 1–5.

НЕ придумывай.
НЕ делай выводы без основания.

НЕ сохраняй:

- пароли;
- адреса;
- документы;
- банковские данные;
- чувствительную информацию;
- случайные эмоции;
- одноразовые сообщения.

Если полезных фактов нет:

NONE

Реальные сообщения:

{chr(10).join(text_parts)}
"""

        # Повторно проверяем перед самим AI-запросом.
        # Это важно, если LOCAL MODE включился
        # пока поток обучения собирал историю.
        if is_local_mode():
            print(
                "🧠 Learning cancelled: "
                "LOCAL MODE activated",
                flush=True,
            )
            return

        learned = ask_learning_model([
            {
                "role": "system",
                "content": (
                    "Ты аккуратный модуль "
                    "долговременного обучения. "
                    "Работай только с фактами "
                    "из сообщений."
                ),
            },
            {
                "role": "user",
                "content": prompt,
            },
        ]).strip()

        if not learned:
            return

        if learned.upper() != "NONE":

            for raw in learned.splitlines():

                line = raw.strip()

                if (
                    not line
                    or line.upper() == "NONE"
                ):
                    continue

                if line.startswith(
                    "USER|"
                ):
                    parts = line.split(
                        "|",
                        2,
                    )

                    if len(parts) != 3:
                        continue

                    _, uid, fact = parts

                    try:
                        numeric_uid = int(
                            uid.strip()
                        )

                    except (
                        ValueError,
                        TypeError,
                    ):
                        continue

                    fact = fact.strip()

                    if not fact:
                        continue

                    name = known_names.get(
                        str(numeric_uid)
                    )

                    save_user_memory(
                        chat_id,
                        numeric_uid,
                        name,
                        fact,
                    )

                elif line.startswith(
                    "CHAT|"
                ):
                    parts = line.split(
                        "|",
                        2,
                    )

                    if len(parts) != 3:
                        continue

                    _, fact, importance = parts

                    fact = fact.strip()

                    if not fact:
                        continue

                    try:
                        importance = int(
                            importance.strip()
                        )

                    except Exception:
                        importance = 1

                    save_knowledge(
                        chat_id,
                        fact,
                        importance,
                    )

        stage = int(
            state.get(
                "development_stage",
                1,
            )
            or 1
        )

        total = get_chat_message_count(
            chat_id
        )

        if (
            stage < 2
            and total >= 300
        ):
            stage = 2

        if (
            stage < 3
            and total >= 1000
        ):
            stage = 3

        if (
            stage < 4
            and total >= 3000
        ):
            stage = 4

        (
            supabase
            .table("bot_learning_state")
            .update({
                "messages_since_learning": 0,
                "development_stage": stage,
                "last_learning_at": utc_now(),
            })
            .eq(
                "chat_id",
                db_chat_id(chat_id),
            )
            .execute()
        )

        learning_retry_until.pop(
            chat_id,
            None,
        )

        print(
            f"🧠 LEARNING COMPLETE | "
            f"chat={chat_id} | "
            f"messages={total} | "
            f"stage={stage}",
            flush=True,
        )

    except Exception as e:
        learning_retry_until[
            chat_id
        ] = (
            time.time()
            + LEARNING_RETRY_TIME
        )

        print(
            "Learning error:",
            e,
            flush=True,
        )

    finally:
        with learning_lock:
            learning_running.discard(
                chat_id
            )


def maybe_learn(chat_id):
    # Самое важное изменение:
    # когда все AI недоступны, счётчик можно продолжать
    # вести, но запускать AI-обучение нельзя.
    count = increase_learning_counter(
        chat_id
    )

    if count < LEARNING_EVERY_MESSAGES:
        return

    if is_local_mode():
        print(
            f"🧠 Learning waiting | "
            f"chat={chat_id} | LOCAL MODE",
            flush=True,
        )
        return

    if not learning_ai_available():
        update_local_mode()

        print(
            f"🧠 Learning waiting | "
            f"chat={chat_id} | no AI",
            flush=True,
        )
        return

    retry_until = (
        learning_retry_until.get(
            chat_id,
            0,
        )
    )

    if time.time() < retry_until:
        return

    with learning_lock:

        if chat_id in learning_running:
            return

        learning_running.add(
            chat_id
        )

    threading.Thread(
        target=perform_learning,
        args=(chat_id,),
        daemon=True,
    ).start()


# =========================================================
# CHAT CONTEXT
# =========================================================

def build_chat_context(
    chat_id,
    user_id,
    user_name,
    text,
):
    messages = [
        {
            "role": "system",
            "content": SYSTEM_PROMPT,
        }
    ]

    state = get_learning_state(
        chat_id
    )

    stage = int(
        state.get(
            "development_stage",
            1,
        )
        or 1
    )

    messages.append({
        "role": "system",
        "content": (
            "Текущая стадия развития:\n"
            +
            DEVELOPMENT_STAGES.get(
                stage,
                DEVELOPMENT_STAGES[1],
            )
        ),
    })

    if user_id is not None:

        emotion_state = get_emotion_state(
            chat_id,
            user_id,
        )

        messages.append({
            "role": "system",
            "content": (
                "=== ТЕКУЩЕЕ НАСТРОЕНИЕ ===\n"
                +
                get_emotion_instruction(
                    emotion_state
                )
                +
                "\nНе сообщай пользователю "
                "числовые значения "
                "этих параметров.\n"
                "=== КОНЕЦ НАСТРОЕНИЯ ==="
            ),
        })

    knowledge = get_knowledge(
        chat_id
    )

    if knowledge:
        lines = []

        for item in knowledge:
            value = (
                item.get(
                    "knowledge"
                )
                or ""
            ).strip()

            if value:
                lines.append(
                    f"- {value}"
                )

        if lines:
            messages.append({
                "role": "system",
                "content": (
                    "Полезная долговременная "
                    "память этого конкретного чата:\n"
                    +
                    "\n".join(lines)
                ),
            })

    tank_rows = get_tank_knowledge_for_text(
        text
    )

    if tank_rows:
        tank_lines = []

        for tank in tank_rows:
            tank_lines.append(
                f"- {tank.get('name', '')} | "
                f"нация: {tank.get('nation', '')} | "
                f"уровень: {tank.get('tier', '')}"
            )

        messages.append({
            "role": "system",
            "content": (
                "Данные из базы Tanks Blitz. "
                "Используй их только как источник "
                "фактов. Не придумывай ТТХ.\n"
                +
                "\n".join(tank_lines)
            ),
        })

    history = get_chat_memory(
        chat_id,
        CHAT_MEMORY_LIMIT,
    )

    current_saved = False

    for item in history:

        role = item.get("role")

        content = (
            item.get("content")
            or ""
        )

        if not content:
            continue

        name = (
            item.get(
                "speaker_name"
            )
            or "Участник"
        )

        sid = str(
            item.get(
                "speaker_id"
            )
            or ""
        )

        if (
            role == "user"
            and sid == str(user_id)
            and content == text
        ):
            current_saved = True

        if role == "user":
            messages.append({
                "role": "user",
                "content": (
                    f"{name}: {content}"
                ),
            })

        elif role == "assistant":
            messages.append({
                "role": "assistant",
                "content": content,
            })

    personal = get_user_memory(
        chat_id,
        user_id,
    )

    if (
        personal
        and personal.get("memory")
    ):
        personal_memory = (
            personal["memory"]
            or ""
        ).strip()

        if personal_memory:
            messages.append({
                "role": "system",
                "content": (
                    "=== ЛИЧНАЯ ПАМЯТЬ "
                    "ТЕКУЩЕГО УЧАСТНИКА ===\n"
                    "Эта память относится "
                    "именно к человеку, "
                    "который сейчас пишет.\n"
                    "Используй её только если "
                    "вопрос относится к факту.\n\n"
                    "ЛИЧНАЯ ПАМЯТЬ:\n"
                    +
                    personal_memory
                    +
                    "\n=== КОНЕЦ ПАМЯТИ ==="
                ),
            })

    if not current_saved:
        messages.append({
            "role": "user",
            "content": (
                f"{user_name or 'Участник'}: "
                f"{text}"
            ),
        })

    return messages


# =========================================================
# QUESTIONS
# =========================================================

QUESTION_WORDS = (
    "кто",
    "что",
    "где",
    "когда",
    "почему",
    "зачем",
    "как",
    "какой",
    "какая",
    "какие",
    "сколько",
    "можно",
    "правда",
    "есть ли",
)


def looks_like_question(text):
    low = text.lower().strip()

    return (
        "?" in low
        or any(
            low.startswith(
                word + " "
            )
            for word in QUESTION_WORDS
        )
    )


# =========================================================
# BOT MENTION
# =========================================================

def is_directed_to_bot_vk(
    message,
    text,
):
    low = text.lower()

    reply = message.get(
        "reply_message"
    )

    if (
        reply
        and str(
            reply.get(
                "from_id",
                "",
            )
        ).startswith("-")
    ):
        return True

    if "[club" in low:
        return True

    return any(
        word in low
        for word in (
            "бот",
            "бонус-коды",
            "бонус коды",
            "эй бот",
        )
    )


def is_directed_to_bot_telegram(
    message,
    text,
):
    low = text.lower()

    reply = (
        message.get(
            "reply_to_message"
        )
        or {}
    )

    reply_from = (
        reply.get("from")
        or {}
    )

    if (
        TELEGRAM_BOT_ID
        and reply_from.get("id")
        == TELEGRAM_BOT_ID
    ):
        return True

    if (
        TELEGRAM_BOT_USERNAME
        and (
            "@"
            + TELEGRAM_BOT_USERNAME.lower()
        ) in low
    ):
        return True

    return any(
        word in low
        for word in (
            "бот",
            "эй бот",
            "бонус-коды",
            "бонус коды",
        )
    )


# =========================================================
# SHOULD ANSWER
# =========================================================

def should_answer(
    message,
    text,
    platform="vk",
):
    text = text.strip()

    if not text:
        return False

    if looks_like_not_my_feature_question(
        text
    ):
        return False

    if platform == "telegram":

        if is_directed_to_bot_telegram(
            message,
            text,
        ):
            return True

    else:

        if is_directed_to_bot_vk(
            message,
            text,
        ):
            return True

    if len(text) <= 1:
        return False

    if looks_like_question(text):
        return True

    words = len(
        text.split()
    )

    if words <= 2:
        return random.random() < 0.20

    if words <= 6:
        return random.random() < 0.40

    if words <= 15:
        return random.random() < 0.60

    return random.random() < 0.72


# =========================================================
# MAIN AI
# =========================================================

def ask_ai(
    chat_id,
    text,
    user_id,
    user_name,
):
    """
    Главная точка входа в AI.

    Порядок:

    1. Обновляем эмоции.
    2. Простое сообщение -> LOCAL без AI.
    3. Если все AI заблокированы -> LOCAL.
    4. Groq 120B.
    5. Groq 20B.
    6. OpenRouter.
    7. Если все провайдеры заблокировались -> LOCAL.
    """

    if user_id is not None:

        emotion_state = process_emotion(
            chat_id,
            int(user_id),
            text,
        )

        short_reaction = (
            get_emotion_short_reaction(
                emotion_state,
                text,
            )
        )

        if short_reaction:
            print(
                "EMOTION SHORT REACTION:",
                short_reaction,
                flush=True,
            )

            return short_reaction

    # -----------------------------------------------------
    # Простые сообщения вообще не отправляем в AI.
    # -----------------------------------------------------

    if not should_use_ai_for_message(
        text
    ):
        print(
            "AI SKIPPED | simple message -> LOCAL",
            flush=True,
        )

        return local_answer(
            chat_id,
            text,
            user_id,
            user_name,
        )

    # -----------------------------------------------------
    # Если все AI уже заблокированы —
    # НИКАКОГО запроса в AI.
    # -----------------------------------------------------

    if is_local_mode():
        print(
            "AI SKIPPED | LOCAL MODE",
            flush=True,
        )

        return local_answer(
            chat_id,
            text,
            user_id,
            user_name,
        )

    # -----------------------------------------------------
    # 1. GROQ
    # -----------------------------------------------------

    try:
        reply = ask_groq(
            chat_id,
            text,
            user_id,
            user_name,
        )

        update_local_mode()

        return reply

    except Exception as groq_error:
        print(
            "Groq final error -> OpenRouter:",
            groq_error,
            flush=True,
        )

    # -----------------------------------------------------
    # После Groq проверяем:
    # вдруг Groq 120B/20B заблокировались,
    # а OpenRouter тоже уже был в cooldown.
    # -----------------------------------------------------

    update_local_mode()

    if is_local_mode():
        print(
            "All AI unavailable after Groq -> LOCAL",
            flush=True,
        )

        return local_answer(
            chat_id,
            text,
            user_id,
            user_name,
        )

    # -----------------------------------------------------
    # 2. OPENROUTER
    # -----------------------------------------------------

    if (
        not OPENROUTER_API_KEY
        or not OPENROUTER_MODELS
    ):
        update_local_mode()

        if is_local_mode():
            return local_answer(
                chat_id,
                text,
                user_id,
                user_name,
            )

        raise RuntimeError(
            "Groq недоступен, "
            "OpenRouter token отсутствует."
        )

    if not provider_available(
        "openrouter"
    ):
        remaining = get_provider_remaining(
            "openrouter"
        )

        print(
            f"OpenRouter skipped: "
            f"cooldown {remaining}s",
            flush=True,
        )

        update_local_mode()

        if is_local_mode():
            return local_answer(
                chat_id,
                text,
                user_id,
                user_name,
            )

        raise RuntimeError(
            "OpenRouter временно недоступен."
        )

    try:
        reply = ask_openrouter(
            chat_id,
            text,
            user_id,
            user_name,
        )

        update_local_mode()

        return reply

    except Exception as openrouter_error:

        if is_rate_limit_error(
            openrouter_error
        ):
            mark_provider_blocked(
                "openrouter",
                get_retry_seconds(
                    openrouter_error,
                    DEFAULT_OPENROUTER_BLOCK,
                ),
            )

        elif is_temporary_ai_error(
            openrouter_error
        ):
            mark_provider_blocked(
                "openrouter",
                TEMP_OPENROUTER_BLOCK,
            )

        print(
            "OpenRouter final error:",
            openrouter_error,
            flush=True,
        )

        update_local_mode()

        # -------------------------------------------------
        # Все AI закончились.
        # Вместо ошибки пользователю —
        # локальный ответ.
        # -------------------------------------------------

        if is_local_mode():
            print(
                "ALL AI BLOCKED -> LOCAL MODE",
                flush=True,
            )

            return local_answer(
                chat_id,
                text,
                user_id,
                user_name,
            )

        raise RuntimeError(
            "Все текстовые AI временно "
            "недоступны."
        )


# =========================================================
# GROQ CHAT
# =========================================================

def ask_groq(
    chat_id,
    text,
    user_id,
    user_name,
):
    messages = build_chat_context(
        chat_id,
        user_id,
        user_name,
        text,
    )

    # -----------------------------------------------------
    # 120B
    # -----------------------------------------------------

    if provider_available(
        "groq_main"
    ):

        try:
            print(
                "Groq -> 120B",
                flush=True,
            )

            return ask_model(
                MAIN_MODEL,
                messages,
                GROQ_MAX_TOKENS,
            )

        except Exception as e:

            mark_provider_error(
                "groq_main",
                e,
                DEFAULT_GROQ_MAIN_BLOCK,
                TEMP_GROQ_MAIN_BLOCK,
            )

            print(
                "120B error:",
                e,
                flush=True,
            )

    else:
        remaining = get_provider_remaining(
            "groq_main"
        )

        print(
            f"120B skipped: "
            f"cooldown {remaining}s",
            flush=True,
        )

    # -----------------------------------------------------
    # 20B
    # -----------------------------------------------------

    if provider_available(
        "groq_backup"
    ):

        try:
            print(
                "Groq -> 20B",
                flush=True,
            )

            return ask_model(
                BACKUP_MODEL,
                messages,
                GROQ_MAX_TOKENS,
            )

        except Exception as e:

            mark_provider_error(
                "groq_backup",
                e,
                DEFAULT_GROQ_BACKUP_BLOCK,
                TEMP_GROQ_BACKUP_BLOCK,
            )

            print(
                "20B error:",
                e,
                flush=True,
            )

    else:
        remaining = get_provider_remaining(
            "groq_backup"
        )

        print(
            f"20B skipped: "
            f"cooldown {remaining}s",
            flush=True,
        )

    update_local_mode()

    raise RuntimeError(
        "Обе модели Groq "
        "временно недоступны."
    )


# =========================================================
# AI RECOVERY PROBES
# =========================================================

def probe_groq():
    """
    Проверка Groq без генерации ответа.

    Используется models.list(), поэтому мы не отправляем
    пользовательский текст и не расходуем completion tokens.
    """

    if not groq_main_configured():
        return False

    try:
        groq.models.list()

        print(
            "AI RECOVERY | Groq API reachable",
            flush=True,
        )

        return True

    except Exception as e:
        print(
            "AI RECOVERY | Groq probe failed:",
            e,
            flush=True,
        )

        return False


def probe_openrouter():
    """
    Проверка OpenRouter через /models.

    Пользовательские сообщения сюда НЕ передаются.
    """

    if not OPENROUTER_API_KEY:
        return False

    try:
        response = requests.get(
            OPENROUTER_MODELS_API,
            headers={
                "Authorization":
                    f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type":
                    "application/json",
            },
            timeout=AI_RECOVERY_PROBE_TIMEOUT,
        )

        if response.status_code == 200:
            data = response.json()

            if isinstance(data, dict):
                print(
                    "AI RECOVERY | "
                    "OpenRouter API reachable",
                    flush=True,
                )

                return True

        print(
            "AI RECOVERY | "
            f"OpenRouter probe HTTP "
            f"{response.status_code}",
            flush=True,
        )

        return False

    except Exception as e:
        print(
            "AI RECOVERY | "
            "OpenRouter probe failed:",
            e,
            flush=True,
        )

        return False


def clear_groq_main_cooldown():
    global main_blocked_until

    with ai_state_lock:
        main_blocked_until = 0


def clear_groq_backup_cooldown():
    global backup_blocked_until

    with ai_state_lock:
        backup_blocked_until = 0


def clear_openrouter_cooldown():
    global openrouter_blocked_until

    with ai_state_lock:
        openrouter_blocked_until = 0


def ai_recovery_loop():
    """
    Отдельный фоновой механизм восстановления.

    Никаких постоянных запросов к AI.

    Проверяет только те провайдеры, чей cooldown
    уже закончился.

    Для Groq/OpenRouter используются API health-style
    endpoints, а не пользовательские сообщения.
    """

    global last_ai_recovery_check

    while True:

        try:
            now = time.time()

            if (
                now - last_ai_recovery_check
                < AI_RECOVERY_CHECK_INTERVAL
            ):
                time.sleep(5)
                continue

            last_ai_recovery_check = now

            cleanup_openrouter_model_cooldowns()

            # -------------------------------------------------
            # Groq 120B
            # -------------------------------------------------

            if (
                groq_main_configured()
                and get_provider_remaining(
                    "groq_main"
                ) <= 0
            ):
                if not provider_available(
                    "groq_main"
                ):
                    if probe_groq():
                        clear_groq_main_cooldown()

                        print(
                            "AI RECOVERY | "
                            "Groq 120B cooldown cleared",
                            flush=True,
                        )

            # -------------------------------------------------
            # Groq 20B
            # -------------------------------------------------

            if (
                groq_backup_configured()
                and get_provider_remaining(
                    "groq_backup"
                ) <= 0
            ):
                if not provider_available(
                    "groq_backup"
                ):
                    if probe_groq():
                        clear_groq_backup_cooldown()

                        print(
                            "AI RECOVERY | "
                            "Groq 20B cooldown cleared",
                            flush=True,
                        )

            # -------------------------------------------------
            # OpenRouter
            # -------------------------------------------------

            if (
                openrouter_configured()
                and get_provider_remaining(
                    "openrouter"
                ) <= 0
            ):
                if not provider_available(
                    "openrouter"
                ):
                    if probe_openrouter():
                        clear_openrouter_cooldown()

                        print(
                            "AI RECOVERY | "
                            "OpenRouter cooldown cleared",
                            flush=True,
                        )

            update_local_mode()

        except Exception as e:
            print(
                "AI recovery loop error:",
                e,
                flush=True,
            )

        time.sleep(5)


# =========================================================
# VK SEND
# =========================================================

def send_message(
    peer_id,
    text,
):
    if not text:
        return

    response = requests.post(
        f"{VK_API}/messages.send",
        data={
            "access_token": VK_TOKEN,
            "v": VK_VERSION,
            "peer_id": int(peer_id),
            "message": text[:4096],
            "random_id": 0,
        },
        timeout=15,
    )

    result = response.json()

    if "error" in result:
        print(
            "VK send error:",
            result["error"],
            flush=True,
        )

    return result


# =========================================================
# TELEGRAM API
# =========================================================

def telegram_call(
    method,
    **kwargs,
):
    if not TELEGRAM_API:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN не установлен"
        )

    response = requests.post(
        f"{TELEGRAM_API}/{method}",
        json=kwargs,
        timeout=30,
    )

    data = response.json()

    if not data.get("ok"):
        raise RuntimeError(
            f"Telegram {method}: {data}"
        )

    return data.get(
        "result"
    )


def send_telegram_message(
    chat_id,
    text,
    reply_to_message_id=None,
):
    if not text:
        return

    payload = {
        "chat_id": int(chat_id),
        "text": text[:4096],
        "disable_web_page_preview": True,
    }

    if reply_to_message_id:
        payload[
            "reply_parameters"
        ] = {
            "message_id":
                int(reply_to_message_id),
        }

    return telegram_call(
        "sendMessage",
        **payload,
    )


# =========================================================
# ACTIVE CHATS
# =========================================================

def register_active_chat(
    platform,
    peer_id,
):
    key = (
        f"{platform}:{peer_id}"
    )

    with activity_lock:
        active_chats[key] = {
            "platform": platform,
            "peer_id": str(peer_id),
            "last": time.time(),
        }


def send_platform_message(
    platform,
    peer_id,
    text,
):
    if platform == "vk":
        return send_message(
            int(peer_id),
            text,
        )

    return send_telegram_message(
        int(peer_id),
        text,
    )


# =========================================================
# ACTIVITY
# =========================================================

def activity_loop():
    while True:

        try:
            now = time.time()

            with activity_lock:
                chats = dict(
                    active_chats
                )

            for key, item in chats.items():

                if (
                    now - item["last"]
                    < 20 * 60
                ):
                    continue

                with activity_lock:
                    if key in active_chats:
                        active_chats[
                            key
                        ]["last"] = now

                if random.random() > 0.35:
                    continue

                prompt = random.choice([
                    (
                        "В чате давно тихо. "
                        "Если есть естественная причина "
                        "оживить разговор, напиши "
                        "одну короткую живую реплику."
                    ),
                    (
                        "В чате тишина. "
                        "Придумай короткую естественную "
                        "реплику обычного участника."
                    ),
                    (
                        "Народ молчит. "
                        "Оживи чат одной короткой "
                        "эмоциональной фразой."
                    ),
                ])

                try:

                    activity_chat_id = int(
                        item["peer_id"]
                    )

                    # ask_ai автоматически использует
                    # LOCAL MODE, если все AI заблокированы.
                    reply = ask_ai(
                        activity_chat_id,
                        prompt,
                        None,
                        None,
                    )

                    if not reply:
                        continue

                    send_platform_message(
                        item["platform"],
                        item["peer_id"],
                        reply,
                    )

                    save_chat_message(
                        activity_chat_id,
                        None,
                        "Бот",
                        "assistant",
                        reply,
                    )

                except Exception as e:
                    print(
                        "Activity error:",
                        e,
                        flush=True,
                    )

            time.sleep(60)

        except Exception as e:
            print(
                "Activity loop error:",
                e,
                flush=True,
            )

            time.sleep(60)


# =========================================================
# HEALTH
# =========================================================

@app.route(
    "/",
    methods=["GET"],
)
def home():
    now = time.time()

    current_local_mode = is_local_mode()

    return {
        "status": "ok",
        "bot": "Tanks Blitz AI",
        "version": BOT_VERSION,
        "build": BOT_BUILD,

        "self_learning": True,
        "personality": "alive",
        "emotions": True,
        "offense_system": True,
        "profanity": True,

        "local_mode": current_local_mode,

        "openrouter":
            bool(OPENROUTER_API_KEY),

        "openrouter_models":
            OPENROUTER_MODELS,

        "groq_120b_available":
            (
                groq_main_configured()
                and now >= main_blocked_until
            ),

        "groq_20b_available":
            (
                groq_backup_configured()
                and now >= backup_blocked_until
            ),

        "openrouter_available":
            (
                openrouter_configured()
                and now >= openrouter_blocked_until
            ),

        "cooldowns": {
            "groq_120b": get_provider_remaining(
                "groq_main"
            ),
            "groq_20b": get_provider_remaining(
                "groq_backup"
            ),
            "openrouter": get_provider_remaining(
                "openrouter"
            ),
        },

        "vision": False,
        "voice": False,
    }, 200


# =========================================================
# VK CALLBACK
# =========================================================

@app.route(
    "/callback",
    methods=["POST"],
)
def callback():

    try:
        data = request.get_json(
            force=True
        ) or {}

        if (
            VK_GROUP_SECRET
            and data.get("secret")
            != VK_GROUP_SECRET
        ):
            return "invalid secret", 403

        event_type = data.get(
            "type"
        )

        if event_type == "confirmation":
            return VK_CONFIRMATION_CODE

        if event_type != "message_new":
            return "ok"

        event_id = data.get(
            "event_id",
            "",
        )

        if already_processed(
            "vk:" + str(event_id)
        ):
            return "ok"

        message = (
            data["object"]["message"]
        )

        peer_id = message[
            "peer_id"
        ]

        sender_id = (
            message.get("from_id")
            or message.get("user_id")
        )

        if (
            sender_id
            and int(peer_id)
            == int(sender_id)
        ):
            return "ok"

        if not sender_id:
            return "ok"

        if int(sender_id) < 0:
            return "ok"

        chat_id = int(peer_id)

        register_active_chat(
            "vk",
            peer_id,
        )

        text = (
            message.get("text")
            or ""
        ).strip()

        user_name = get_vk_user_name(
            sender_id
        )

        if not text:
            return "ok"

        save_chat_message(
            chat_id,
            sender_id,
            user_name,
            "user",
            text,
        )

        save_explicit_user_memory(
            chat_id,
            sender_id,
            user_name,
            text,
        )

        maybe_learn(
            chat_id
        )

        if not should_answer(
            message,
            text,
            "vk",
        ):
            return "ok"

        reply = ask_ai(
            chat_id,
            text,
            str(sender_id),
            user_name,
        )

        if reply:

            save_chat_message(
                chat_id,
                None,
                "Бот",
                "assistant",
                reply,
            )

            send_message(
                peer_id,
                reply,
            )

        return "ok"

    except Exception as e:

        print(
            "Callback error:",
            e,
            flush=True,
        )

        return "ok"


# =========================================================
# TELEGRAM WEBHOOK
# =========================================================

@app.route(
    "/telegram/webhook/<secret>",
    methods=["POST"],
)
def telegram_webhook(
    secret,
):
    if not TELEGRAM_BOT_TOKEN:
        return "ok"

    expected = hashlib.sha256(
        TELEGRAM_BOT_TOKEN.encode()
    ).hexdigest()[:32]

    if secret != expected:
        return "forbidden", 403

    try:
        data = request.get_json(
            force=True
        ) or {}

        update_id = data.get(
            "update_id"
        )

        if already_processed(
            "tg:" + str(update_id)
        ):
            return "ok"

        message = data.get(
            "message"
        )

        if not message:
            return "ok"

        sender = (
            message.get("from")
            or {}
        )

        if sender.get(
            "is_bot"
        ):
            return "ok"

        chat = (
            message.get("chat")
            or {}
        )

        raw_chat_id = chat.get(
            "id"
        )

        sender_id = sender.get(
            "id"
        )

        if (
            raw_chat_id is None
            or sender_id is None
        ):
            return "ok"

        chat_id = int(
            raw_chat_id
        )

        register_active_chat(
            "telegram",
            raw_chat_id,
        )

        user_name = (
            get_telegram_user_name(
                sender
            )
        )

        text = (
            message.get("text")
            or message.get("caption")
            or ""
        ).strip()

        if not text:
            return "ok"

        save_chat_message(
            chat_id,
            sender_id,
            user_name,
            "user",
            text,
        )

        save_explicit_user_memory(
            chat_id,
            sender_id,
            user_name,
            text,
        )

        maybe_learn(
            chat_id
        )

        if not should_answer(
            message,
            text,
            "telegram",
        ):
            return "ok"

        reply = ask_ai(
            chat_id,
            text,
            str(sender_id),
            user_name,
        )

        if reply:

            save_chat_message(
                chat_id,
                None,
                "Бот",
                "assistant",
                reply,
            )

            send_telegram_message(
                raw_chat_id,
                reply,
                message.get(
                    "message_id"
                ),
            )

        return "ok"

    except Exception as e:

        print(
            "Telegram webhook error:",
            e,
            flush=True,
        )

        return "ok"


# =========================================================
# TELEGRAM SETUP
# =========================================================

def setup_telegram():
    global TELEGRAM_BOT_ID
    global TELEGRAM_BOT_USERNAME

    if not TELEGRAM_BOT_TOKEN:
        return

    try:
        me = telegram_call(
            "getMe"
        )

        TELEGRAM_BOT_ID = me.get(
            "id"
        )

        TELEGRAM_BOT_USERNAME = (
            me.get(
                "username",
                "",
            )
        )

        external = (
            os.environ.get(
                "RENDER_EXTERNAL_URL",
                "",
            )
            .strip()
            .rstrip("/")
        )

        if not external:

            host = (
                os.environ.get(
                    "RENDER_EXTERNAL_HOSTNAME",
                    "",
                )
                .strip()
            )

            external = (
                f"https://{host}"
                if host
                else ""
            )

        if not external:

            print(
                "Telegram: Render URL "
                "не найден — webhook "
                "не установлен.",
                flush=True,
            )

            return

        secret = hashlib.sha256(
            TELEGRAM_BOT_TOKEN.encode()
        ).hexdigest()[:32]

        webhook_url = (
            f"{external}"
            f"/telegram/webhook/"
            f"{secret}"
        )

        telegram_call(
            "setWebhook",
            url=webhook_url,
            allowed_updates=[
                "message"
            ],
            drop_pending_updates=False,
        )

        print(
            "Telegram connected: "
            f"@{TELEGRAM_BOT_USERNAME} "
            "| webhook enabled",
            flush=True,
        )

    except Exception as e:

        print(
            "Telegram setup error:",
            e,
            flush=True,
        )


# =========================================================
# START
# =========================================================

if __name__ == "__main__":

    print(
        "========================================",
        flush=True,
    )

    print(
        f"🤖 BOT VERSION: {BOT_VERSION}",
        flush=True,
    )

    print(
        f"🧠 BUILD: {BOT_BUILD}",
        flush=True,
    )

    print(
        "🔥 Personality: ALIVE",
        flush=True,
    )

    print(
        "🤬 Profanity: ENABLED",
        flush=True,
    )

    print(
        "😒 Emotions: ENABLED",
        flush=True,
    )

    print(
        "😡 Offense system: ENABLED",
        flush=True,
    )

    print(
        "😂 Humor: ENABLED",
        flush=True,
    )

    print(
        "🧠 Self-learning: ENABLED",
        flush=True,
    )

    print(
        "🟡 Automatic LOCAL MODE: ENABLED",
        flush=True,
    )

    print(
        "🔄 Automatic AI recovery: ENABLED",
        flush=True,
    )

    print(
        f"🧠 MAIN MODEL: {MAIN_MODEL}",
        flush=True,
    )

    print(
        f"🔄 BACKUP MODEL: {BACKUP_MODEL}",
        flush=True,
    )

    print(
        "🆓 OPENROUTER MODELS: "
        + ", ".join(
            OPENROUTER_MODELS
        ),
        flush=True,
    )

    print(
        "🌐 OpenRouter token: "
        + (
            "YES"
            if OPENROUTER_API_KEY
            else "NO"
        ),
        flush=True,
    )

    print(
        "🖼 Image processing: DISABLED",
        flush=True,
    )

    print(
        "🎤 Voice processing: DISABLED",
        flush=True,
    )

    print(
        "📱 Telegram token: "
        + (
            "YES"
            if TELEGRAM_BOT_TOKEN
            else "NO"
        ),
        flush=True,
    )

    print(
        f"🧠 Learning every: "
        f"{LEARNING_EVERY_MESSAGES} messages",
        flush=True,
    )

    print(
        f"💬 Chat context: "
        f"{CHAT_MEMORY_LIMIT} messages",
        flush=True,
    )

    print(
        f"📚 Knowledge context: "
        f"{KNOWLEDGE_LIMIT} records",
        flush=True,
    )

    print(
        f"👤 User memory: "
        f"{USER_MEMORY_LIMIT} facts",
        flush=True,
    )

    print(
        "========================================",
        flush=True,
    )

    # -----------------------------------------------------
    # При старте сразу определяем режим.
    # -----------------------------------------------------

    update_local_mode()

    # -----------------------------------------------------
    # Telegram
    # -----------------------------------------------------

    if TELEGRAM_BOT_TOKEN:
        setup_telegram()

    # -----------------------------------------------------
    # Фоновая активность
    # -----------------------------------------------------

    threading.Thread(
        target=activity_loop,
        daemon=True,
    ).start()

    # -----------------------------------------------------
    # Фоновое восстановление AI
    # -----------------------------------------------------

    threading.Thread(
        target=ai_recovery_loop,
        daemon=True,
    ).start()

    port = int(
        os.environ.get(
            "PORT",
            5000,
        )
    )

    app.run(
        host="0.0.0.0",
        port=port,
    )
