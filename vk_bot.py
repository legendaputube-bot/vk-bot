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

BOT_VERSION = "V1.3"

BOT_BUILD = (
    "Умное самообучение + "
    "админ-панель + "
    "защита участников + "
    "официальная память Tanks Blitz + "
    "VK + Telegram + OpenRouter"
)


# =========================================================
# ADMIN
# =========================================================

ADMIN_ID = 948950706

ADMIN_IDS = {
    ADMIN_ID
}

TESTER_IDS = {
    ADMIN_ID
}

ADMIN_NICK = "Blitz"

INTERVENTION_COOLDOWN = 90

ADMIN_ERROR_COOLDOWN = 300


# =========================================================
# VK
# =========================================================

VK_TOKEN = os.environ.get(
    "VK_TOKEN",
    ""
).strip()

VK_CONFIRMATION_CODE = os.environ.get(
    "VK_CONFIRMATION_CODE",
    ""
).strip()

VK_GROUP_SECRET = os.environ.get(
    "VK_GROUP_SECRET",
    ""
).strip()

ALLOWED_VK_PEER_ID = int(
    os.environ.get(
        "ALLOWED_VK_PEER_ID",
        "0"
    )
)


# =========================================================
# TELEGRAM
# =========================================================

TELEGRAM_BOT_TOKEN = os.environ.get(
    "TELEGRAM_BOT_TOKEN",
    ""
).strip()


# =========================================================
# AI
# =========================================================

GROQ_API_KEY = os.environ.get(
    "GROQ_API_KEY",
    ""
).strip()

OPENROUTER_API_KEY = os.environ.get(
    "OPENROUTER_API_KEY",
    ""
).strip()


# =========================================================
# SUPABASE
# =========================================================

SUPABASE_URL = os.environ.get(
    "SUPABASE_URL",
    ""
).strip()

SUPABASE_SECRET_KEY = os.environ.get(
    "SUPABASE_SECRET_KEY",
    ""
).strip()

if SUPABASE_URL and not SUPABASE_URL.startswith(
    ("http://", "https://")
):
    SUPABASE_URL = "https://" + SUPABASE_URL

supabase = create_client(
    SUPABASE_URL,
    SUPABASE_SECRET_KEY
)


# =========================================================
# API
# =========================================================

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


# =========================================================
# MODELS
# =========================================================

MAIN_MODEL = "openai/gpt-oss-120b"

BACKUP_MODEL = "openai/gpt-oss-20b"

OPENROUTER_MODEL = "openrouter/free"


# =========================================================
# LIMITS
# =========================================================

GROQ_MAX_TOKENS = 320

OPENROUTER_MAX_TOKENS = 320

LEARNING_MAX_TOKENS = 300

INTERVENTION_MAX_TOKENS = 140

CHAT_MEMORY_LIMIT = 18

LEARNING_HISTORY_LIMIT = 60

LEARNING_EVERY_MESSAGES = 40

KNOWLEDGE_LIMIT = 8

USER_MEMORY_LIMIT = 10

OFFICIAL_KNOWLEDGE_LIMIT = 20

NAME_CACHE_TIME = 24 * 60 * 60

EVENT_CACHE_TIME = 30 * 60

EVENT_CACHE_LIMIT = 2000

LEARNING_RETRY_TIME = 10 * 60


# =========================================================
# MEMORY / CACHE
# =========================================================

user_names = {}

tg_user_names = {}

processed_events = {}

active_chats = {}

activity_lock = threading.Lock()

learning_running = set()

learning_lock = threading.Lock()

learning_retry_until = {}

main_blocked_until = 0

backup_blocked_until = 0

TELEGRAM_BOT_ID = None

TELEGRAM_BOT_USERNAME = ""

intervention_until = {}

intervention_lock = threading.Lock()

admin_error_cache = {}

admin_error_lock = threading.Lock()

# Временное отключение обычных ответов.
# Не влияет на память, официальные знания и обучение.
response_disabled = set()

response_mode_lock = threading.Lock()


# =========================================================
# FLASK / GROQ
# =========================================================

app = Flask(__name__)

groq = Groq(
    api_key=GROQ_API_KEY
)


# =========================================================
# DEVELOPMENT STAGES
# =========================================================

DEVELOPMENT_STAGES = {
    1: (
        "Ты только начинаешь знакомиться с чатом. "
        "Больше наблюдай, чем вмешивайся."
    ),

    2: (
        "Ты уже немного освоился и понимаешь "
        "часть людей и контекста."
    ),

    3: (
        "Ты уже свой участник этого чата. "
        "Хорошо чувствуешь атмосферу."
    ),

    4: (
        "Ты давно в чате и хорошо понимаешь "
        "локальный контекст и атмосферу."
    )
}


# =========================================================
# SYSTEM PROMPT
# =========================================================

SYSTEM_PROMPT = """
Ты — живой AI-участник общего чата сообщества.

Ты НЕ модератор.
Ты НЕ администратор.
Ты НЕ сотрудник поддержки.

Ты обычный участник разговора.

Главное правило:

НЕ пытайся отвечать на каждое сообщение.

Если люди разговаривают между собой и тебя
не спрашивают — обычно молчи.

Если человеку нечего сказать — молчи.

=== ОБРАЩЕНИЕ К ТЕБЕ ===

Если человек явно обращается к тебе:
«бот»
«эй бот»
«Блиц бот»
или отвечает на твоё сообщение —
можно отвечать.

Если человек просто разговаривает с другим
участником — не вмешивайся.

Не считай любой вопрос в чате вопросом к себе.

Например:

«кто завтра играет?»

сам по себе НЕ означает, что спрашивают тебя.

Но если:

«бот, кто завтра играет?»

тогда отвечай.

=== TANKS BLITZ ===

Tanks Blitz — главная игровая тема сообщества.

Если тебя прямо спрашивают про Tanks Blitz,
отвечай по существу.

Не выдумывай:
- ТТХ;
- броню;
- урон;
- пробитие;
- перезарядку;
- карты;
- события;
- бонус-коды;
- обновления;
- механики.

Не смешивай Tanks Blitz
с World of Tanks PC.

Если точных данных нет —
скажи, что не уверен.

Лучше честно сказать «не знаю»,
чем придумать красивый ответ.

=== ОФИЦИАЛЬНАЯ ПАМЯТЬ ===

Если предоставлена официальная память Tanks Blitz,
она добавлена главным администратором.

Она важнее обычной памяти чата.

Не изменяй её по сообщениям обычных пользователей.

=== ПАМЯТЬ УЧАСТНИКОВ ===

Если есть личная память текущего пользователя,
используй её только если она относится
к текущему вопросу.

Не придумывай личные факты.

Не используй память одного человека
для другого.

Не раскрывай внутреннюю память.

Не говори:
«я записал это в базу»
или
«я посмотрел свою базу»,
если пользователь специально не спрашивает
о технической работе бота.

Если человек говорит:
«запомни»
— это просьба сохранить информацию.

=== СТИЛЬ ===

Говори естественно.

Можно быть дерзким, ироничным и живым.

Но не превращай каждую фразу в шутку.

Не пиши длинные лекции без просьбы.

Обычно достаточно 1–3 коротких абзацев.

Не повторяй сообщение пользователя.

Не начинай каждый ответ одинаково.

Не вставляй Tanks Blitz в разговор,
если разговор вообще не об игре.

=== ЛИЧНЫЕ ТЕМЫ ===

Не придумывай медицинские диагнозы.

Не назначай лекарства и дозировки.

Не выдавай опасные советы.

Если тема не связана с игрой
и тебя прямо спрашивают —
отвечай осторожно и по существу.

=== ЗАЩИТА УЧАСТНИКОВ ===

Если другой участник действительно
унижает человека, можно вмешаться.

Но не превращай обычный спор
в конфликт.

Не атакуй:
- семью;
- здоровье;
- внешность;
- национальность;
- религию;
- другие чувствительные признаки.

Не угрожай.

Не трави человека.

Если вмешиваешься —
коротко, дерзко, с юмором.

=== ГЛАВНОЕ ===

Сначала пойми:
к тебе обращаются или нет.

Потом:
есть ли тебе что добавить.

Если нет —
молчи.
"""


# =========================================================
# GAME RELEVANCE FILTER
# =========================================================

GAME_KEYWORDS = (
    "tanks blitz",
    "танкс блиц",
    "танки блиц",
    "wot blitz",
    "блиц",
    "танк",
    "танка",
    "танке",
    "танков",
    "танки",
    "брон",
    "урон",
    "пробит",
    "оруд",
    "пушка",
    "башн",
    "гусл",
    "двигател",
    "модул",
    "экипаж",
    "перезаряд",
    "снаряд",
    "голд",
    "серебр",
    "опыт",
    "хп",
    "хпшк",
    "ттх",
    "карта",
    "карты",
    "режим",
    "взвод",
    "бой",
    "боев",
    "катка",
    "катки",
    "побед",
    "поражен",
    "клан",
    "арена",
    "бонус код",
    "бонус-код",
    "контейнер",
    "контейнеры",
    "прем",
    "премиум",
    "ис-7",
    "ис 7",
    "е100",
    "e100",
    "шеридан",
    "леопард",
    "объект",
    "объект 140",
    "т-62",
    "т-55",
    "т55",
    "т62",
    "чар",
    "char mle",
)


def is_game_relevant(text):
    """
    Быстрый фильтр перед самообучением.

    Если в сообщении нет игровой тематики,
    AI-модель обучения вообще не вызывается.
    """

    low = normalize_text(text).lower()

    if not low:
        return False

    return any(
        keyword in low
        for keyword in GAME_KEYWORDS
    )


def filter_learning_history(history):
    """
    Оставляем только сообщения,
    которые похожи на Tanks Blitz-контекст.
    """

    result = []

    for item in history:

        content = (
            item.get("content")
            or ""
        )

        if not content:
            continue

        if is_game_relevant(content):
            result.append(item)

    return result


# =========================================================
# HELPERS
# =========================================================

def utc_now():
    return datetime.now(
        timezone.utc
    ).isoformat()


def db_chat_id(chat_id):
    return int(chat_id)


def db_user_id(user_id):
    return int(user_id)


def normalize_text(text):
    return re.sub(
        r"\s+",
        " ",
        (text or "").strip()
    )


# =========================================================
# ADMIN
# =========================================================

def is_admin(user_id):

    try:
        return int(user_id) in ADMIN_IDS

    except (
        ValueError,
        TypeError
    ):
        return False


def is_tester(user_id):

    try:
        return int(user_id) in TESTER_IDS

    except (
        ValueError,
        TypeError
    ):
        return False


# =========================================================
# ADMIN PRIVATE MESSAGE
# =========================================================

def send_vk_private_message(
    user_id,
    text
):

    if not is_admin(user_id):
        return None

    if not text:
        return None

    try:

        response = requests.post(
            f"{VK_API}/messages.send",
            data={
                "access_token": VK_TOKEN,
                "v": VK_VERSION,
                "peer_id": int(user_id),
                "message": text[:4096],
                "random_id": random.randint(
                    1,
                    2147483647
                )
            },
            timeout=15
        )

        result = response.json()

        if "error" in result:

            print(
                "ADMIN PRIVATE MESSAGE ERROR:",
                result["error"],
                flush=True
            )

        return result

    except Exception as e:

        print(
            "Admin private message exception:",
            e,
            flush=True
        )

        return None


def notify_admin_error(
    error_type,
    error,
    context=""
):

    try:

        error_text = normalize_text(
            str(error)
        )

        context_text = normalize_text(
            context
        )

        cache_key = (
            f"{error_type}|"
            f"{error_text[:300]}|"
            f"{context_text[:150]}"
        )

        now = time.time()

        with admin_error_lock:

            previous = admin_error_cache.get(
                cache_key,
                0
            )

            if (
                now - previous
                < ADMIN_ERROR_COOLDOWN
            ):
                return

            admin_error_cache[
                cache_key
            ] = now

        message = (
            "⚠️ ОШИБКА БОТА\n\n"
            f"Тип: {error_type}\n"
            f"Ошибка: {error_text[:1200]}"
        )

        if context_text:
            message += (
                f"\nКонтекст: "
                f"{context_text[:500]}"
            )

        send_vk_private_message(
            ADMIN_ID,
            message
        )

    except Exception as e:

        print(
            "Admin error notify failed:",
            e,
            flush=True
        )


# =========================================================
# RESPONSE MODE
# =========================================================

def set_response_enabled(
    chat_id,
    enabled
):

    key = str(chat_id)

    with response_mode_lock:

        if enabled:
            response_disabled.discard(key)
        else:
            response_disabled.add(key)


def is_response_enabled(chat_id):

    with response_mode_lock:

        return str(chat_id) not in response_disabled


# =========================================================
# VK VALIDATION
# =========================================================

def is_vk_group_chat(
    peer_id,
    sender_id=None
):

    try:
        peer_id = int(peer_id)

    except (
        ValueError,
        TypeError
    ):
        return False

    if peer_id < 2000000000:
        return False

    if (
        ALLOWED_VK_PEER_ID
        and peer_id != ALLOWED_VK_PEER_ID
    ):
        return False

    return True


def is_allowed_vk_chat(peer_id):

    try:
        peer_id = int(peer_id)

    except (
        ValueError,
        TypeError
    ):
        return False

    return is_vk_group_chat(peer_id)


# =========================================================
# EVENT PROTECTION
# =========================================================

def already_processed(event_id):

    if not event_id:
        return False

    now = time.time()

    for key in list(processed_events):

        if (
            now - processed_events[key]
            > EVENT_CACHE_TIME
        ):
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
            "too many requests",
            "quota"
        )
    )


def get_retry_seconds(
    error,
    default
):

    match = re.search(
        r"try again in\s+"
        r"(?:(\d+)h)?"
        r"(?:(\d+)m)?"
        r"(?:(\d+(?:\.\d+)?)s)?",
        str(error),
        re.I
    )

    if not match:
        return default

    total = (
        int(match.group(1) or 0) * 3600
        +
        int(match.group(2) or 0) * 60
        +
        float(match.group(3) or 0)
    )

    if total <= 0:
        return default

    return int(total) + 10


# =========================================================
# VK USER NAME
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
                "user_ids": user_id
            },
            timeout=10
        ).json()

        users = data.get(
            "response",
            []
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
                name
            )

        return name or None

    except Exception as e:

        print(
            "VK name error:",
            e,
            flush=True
        )

        return None


# =========================================================
# TELEGRAM USER NAME
# =========================================================

def get_telegram_user_name(user):

    if not user:
        return None

    uid = str(
        user.get(
            "id",
            ""
        )
    )

    cached = tg_user_names.get(
        uid
    )

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

        name = user.get(
            "username",
            ""
        ).strip()

    if name:

        tg_user_names[uid] = (
            time.time(),
            name
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
    content
):

    if (
        chat_id is None
        or not content
    ):
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
                TypeError
            ):

                database_speaker_id = None

        (
            supabase
            .table("bot_chat_memory")
            .insert({
                "chat_id":
                    db_chat_id(chat_id),

                "speaker_id":
                    database_speaker_id,

                "speaker_name":
                    speaker_name or "",

                "role":
                    role,

                "content":
                    str(content)[:4000]
            })
            .execute()
        )

    except Exception as e:

        print(
            "Chat memory save error:",
            e,
            flush=True
        )


def get_chat_memory(
    chat_id,
    limit=CHAT_MEMORY_LIMIT
):

    try:

        result = (
            supabase
            .table("bot_chat_memory")
            .select(
                "speaker_id, speaker_name, "
                "role, content"
            )
            .eq(
                "chat_id",
                db_chat_id(chat_id)
            )
            .order(
                "created_at",
                desc=True
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
            flush=True
        )

        return []


def get_chat_message_count(chat_id):

    try:

        result = (
            supabase
            .table("bot_chat_memory")
            .select(
                "id",
                count="exact",
                head=True
            )
            .eq(
                "chat_id",
                db_chat_id(chat_id)
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
            flush=True
        )

        return 0


# =========================================================
# KNOWLEDGE
# =========================================================

def knowledge_fingerprint(
    chat_id,
    knowledge
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
    importance=1
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
            knowledge
        )

        existing = (
            supabase
            .table("bot_knowledge")
            .select("id")
            .eq(
                "chat_id",
                database_chat_id
            )
            .eq(
                "fingerprint",
                fingerprint
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
                "chat_id":
                    database_chat_id,

                "knowledge":
                    knowledge[:2000],

                "importance":
                    max(
                        1,
                        min(
                            int(importance),
                            5
                        )
                    ),

                "fingerprint":
                    fingerprint
            })
            .execute()
        )

        print(
            "NEW KNOWLEDGE:",
            knowledge[:150],
            flush=True
        )

    except Exception as e:

        print(
            "Knowledge save error:",
            e,
            flush=True
        )


def get_knowledge(
    chat_id,
    limit=KNOWLEDGE_LIMIT
):

    try:

        result = (
            supabase
            .table("bot_knowledge")
            .select(
                "id, knowledge, importance, "
                "created_at"
            )
            .eq(
                "chat_id",
                db_chat_id(chat_id)
            )
            .order(
                "importance",
                desc=True
            )
            .order(
                "created_at",
                desc=True
            )
            .limit(limit)
            .execute()
        )

        return result.data or []

    except Exception as e:

        print(
            "Knowledge load error:",
            e,
            flush=True
        )

        return []


def get_all_knowledge(
    chat_id,
    limit=50
):

    try:

        result = (
            supabase
            .table("bot_knowledge")
            .select(
                "id, knowledge, importance, "
                "created_at"
            )
            .eq(
                "chat_id",
                db_chat_id(chat_id)
            )
            .order(
                "created_at",
                desc=True
            )
            .limit(limit)
            .execute()
        )

        return result.data or []

    except Exception as e:

        print(
            "All knowledge load error:",
            e,
            flush=True
        )

        return []


def delete_knowledge_by_id(
    chat_id,
    knowledge_id
):

    try:

        (
            supabase
            .table("bot_knowledge")
            .delete()
            .eq(
                "id",
                int(knowledge_id)
            )
            .eq(
                "chat_id",
                db_chat_id(chat_id)
            )
            .execute()
        )

        return True

    except Exception as e:

        notify_admin_error(
            "Knowledge delete",
            e,
            f"chat={chat_id} id={knowledge_id}"
        )

        return False


def clear_knowledge(
    chat_id
):

    try:

        (
            supabase
            .table("bot_knowledge")
            .delete()
            .eq(
                "chat_id",
                db_chat_id(chat_id)
            )
            .execute()
        )

        return True

    except Exception as e:

        notify_admin_error(
            "Knowledge clear",
            e,
            f"chat={chat_id}"
        )

        return False


# =========================================================
# OFFICIAL TANKS BLITZ MEMORY
# =========================================================

def official_fingerprint(
    chat_id,
    title,
    content
):

    raw = (
        f"{chat_id}|"
        f"{normalize_text(title).lower()}|"
        f"{normalize_text(content).lower()}"
    )

    return hashlib.sha256(
        raw.encode("utf-8")
    ).hexdigest()


def get_official_knowledge(
    chat_id,
    limit=OFFICIAL_KNOWLEDGE_LIMIT
):

    try:

        result = (
            supabase
            .table("bot_official_knowledge")
            .select(
                "id, title, content, "
                "created_at, updated_at"
            )
            .eq(
                "chat_id",
                db_chat_id(chat_id)
            )
            .order(
                "updated_at",
                desc=True
            )
            .limit(limit)
            .execute()
        )

        return result.data or []

    except Exception as e:

        print(
            "Official knowledge load error:",
            e,
            flush=True
        )

        notify_admin_error(
            "Supabase official memory load",
            e,
            f"chat={chat_id}"
        )

        return []


def save_official_knowledge(
    chat_id,
    title,
    content
):

    title = normalize_text(title)
    content = normalize_text(content)

    if not title or not content:

        return False, (
            "❌ Нужно указать название и содержимое."
        )

    if len(title) > 100:

        return False, (
            "❌ Название слишком длинное."
        )

    if len(content) > 6000:

        return False, (
            "❌ Содержимое слишком длинное."
        )

    try:

        database_chat_id = db_chat_id(
            chat_id
        )

        existing = (
            supabase
            .table("bot_official_knowledge")
            .select("id")
            .eq(
                "chat_id",
                database_chat_id
            )
            .eq(
                "title",
                title
            )
            .limit(1)
            .execute()
        )

        if existing.data:

            return False, (
                "❌ Такая запись уже существует.\n"
                "Используй !tbupdate"
            )

        fingerprint = official_fingerprint(
            database_chat_id,
            title,
            content
        )

        (
            supabase
            .table("bot_official_knowledge")
            .insert({
                "chat_id":
                    database_chat_id,

                "title":
                    title,

                "content":
                    content,

                "fingerprint":
                    fingerprint
            })
            .execute()
        )

        return True, (
            f"✅ Добавлено:\n📚 {title}"
        )

    except Exception as e:

        notify_admin_error(
            "Official memory save",
            e,
            f"chat={chat_id} title={title}"
        )

        return False, (
            "❌ Не удалось сохранить запись."
        )


def update_official_knowledge(
    chat_id,
    title,
    content
):

    title = normalize_text(title)
    content = normalize_text(content)

    if not title or not content:

        return False, (
            "❌ Укажи название и новый текст."
        )

    try:

        existing = (
            supabase
            .table("bot_official_knowledge")
            .select("id")
            .eq(
                "chat_id",
                db_chat_id(chat_id)
            )
            .eq(
                "title",
                title
            )
            .limit(1)
            .execute()
        )

        if not existing.data:

            return False, (
                "❌ Такой записи нет."
            )

        fingerprint = official_fingerprint(
            chat_id,
            title,
            content
        )

        (
            supabase
            .table("bot_official_knowledge")
            .update({
                "content":
                    content[:6000],

                "fingerprint":
                    fingerprint,

                "updated_at":
                    utc_now()
            })
            .eq(
                "id",
                existing.data[0]["id"]
            )
            .execute()
        )

        return True, (
            f"✅ Обновлено:\n📚 {title}"
        )

    except Exception as e:

        notify_admin_error(
            "Official memory update",
            e,
            f"chat={chat_id} title={title}"
        )

        return False, (
            "❌ Не удалось обновить запись."
        )


def delete_official_knowledge(
    chat_id,
    title
):

    title = normalize_text(title)

    if not title:

        return False, (
            "❌ Укажи название."
        )

    try:

        existing = (
            supabase
            .table("bot_official_knowledge")
            .select("id")
            .eq(
                "chat_id",
                db_chat_id(chat_id)
            )
            .eq(
                "title",
                title
            )
            .limit(1)
            .execute()
        )

        if not existing.data:

            return False, (
                "❌ Такой записи нет."
            )

        (
            supabase
            .table("bot_official_knowledge")
            .delete()
            .eq(
                "id",
                existing.data[0]["id"]
            )
            .execute()
        )

        return True, (
            f"🗑 Удалено:\n📚 {title}"
        )

    except Exception as e:

        notify_admin_error(
            "Official memory delete",
            e,
            f"chat={chat_id} title={title}"
        )

        return False, (
            "❌ Не удалось удалить."
        )


def official_memory_text(chat_id):

    records = get_official_knowledge(
        chat_id,
        OFFICIAL_KNOWLEDGE_LIMIT
    )

    if not records:

        return (
            "📚 Официальная память Tanks Blitz "
            "пока пустая."
        )

    lines = [
        "📚 ОФИЦИАЛЬНАЯ ПАМЯТЬ:",
        ""
    ]

    for index, item in enumerate(
        records,
        start=1
    ):

        title = (
            item.get("title")
            or "Без названия"
        )

        content = normalize_text(
            item.get("content")
            or ""
        )

        if len(content) > 180:
            content = content[:180] + "..."

        lines.append(
            f"{index}. {title}"
        )

        lines.append(
            f"   {content}"
        )

    return "\n".join(lines)


# =========================================================
# TB COMMANDS
# =========================================================

def handle_admin_tb_command(
    chat_id,
    sender_id,
    text
):

    if not is_admin(sender_id):

        return False, None

    raw = (text or "").strip()

    if not raw.lower().startswith("!tb"):

        return False, None

    lower = raw.lower()

    if lower == "!tbhelp":

        return True, (
            "🛠 ОФИЦИАЛЬНАЯ ПАМЯТЬ\n\n"
            "!tbadd название | текст\n"
            "!tbupdate название | новый текст\n"
            "!tbdelete название\n"
            "!tbmemory\n"
            "!tbhelp\n\n"
            "Доступ только администрации."
        )

    if lower == "!tbmemory":

        return True, official_memory_text(
            chat_id
        )

    if lower.startswith("!tbadd "):

        payload = raw[7:].strip()

        if "|" not in payload:

            return True, (
                "❌ !tbadd название | текст"
            )

        title, content = payload.split(
            "|",
            1
        )

        _, reply = save_official_knowledge(
            chat_id,
            title,
            content
        )

        return True, reply

    if lower.startswith("!tbupdate "):

        payload = raw[10:].strip()

        if "|" not in payload:

            return True, (
                "❌ !tbupdate название | новый текст"
            )

        title, content = payload.split(
            "|",
            1
        )

        _, reply = update_official_knowledge(
            chat_id,
            title,
            content
        )

        return True, reply

    if lower.startswith("!tbdelete "):

        title = raw[10:].strip()

        _, reply = delete_official_knowledge(
            chat_id,
            title
        )

        return True, reply

    return True, (
        "❓ Неизвестная команда.\n"
        "Напиши !tbhelp"
    )


# =========================================================
# USER MEMORY
# =========================================================

def merge_memory(
    old_memory,
    new_fact
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
    memory
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
                database_chat_id
            )
            .eq(
                "user_id",
                database_user_id
            )
            .limit(1)
            .execute()
        )

        old_memory = (
            existing.data[0].get(
                "memory",
                ""
            )
            if existing.data
            else ""
        )

        old_name = (
            existing.data[0].get(
                "name",
                ""
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
            memory
        )[:3000]

        data = {
            "chat_id":
                database_chat_id,

            "user_id":
                database_user_id,

            "name":
                final_name,

            "memory":
                final_memory,

            "updated_at":
                utc_now()
        }

        if existing.data:

            (
                supabase
                .table("bot_users")
                .update(data)
                .eq(
                    "id",
                    existing.data[0]["id"]
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
            "User memory save error:",
            e,
            flush=True
        )


def get_user_memory(
    chat_id,
    user_id
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
                db_chat_id(chat_id)
            )
            .eq(
                "user_id",
                db_user_id(user_id)
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
            flush=True
        )

        return None


def save_explicit_user_memory(
    chat_id,
    user_id,
    user_name,
    text
):

    original = (text or "").strip()

    if not original:
        return False

    fact = None

    match = re.search(
        r"(?:запомни|запомни\s+это|"
        r"запомни\s+пожалуйста)"
        r"\s*[:,-]?\s*"
        r"(?:что\s+)?"
        r"(.+)$",
        original,
        re.IGNORECASE
    )

    if match:

        statement = (
            match.group(1)
            or ""
        ).strip()

        if statement:

            tank_match = re.search(
                r"мой\s+любим(?:ый|ая|ое|ые)"
                r"\s+танк(?:а|ов)?"
                r"\s*(?:—|-|:|=|это|есть)?\s*"
                r"(.+)$",
                statement,
                re.IGNORECASE
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
        "адрес проживания"
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
        fact
    )

    return True


# =========================================================
# LEARNING STATE
# =========================================================

def get_learning_state(chat_id):

    database_chat_id = db_chat_id(
        chat_id
    )

    try:

        result = (
            supabase
            .table("bot_learning_state")
            .select("*")
            .eq(
                "chat_id",
                database_chat_id
            )
            .limit(1)
            .execute()
        )

        if result.data:

            state = result.data[0]

            if "learning_enabled" not in state:
                state["learning_enabled"] = True

            return state

        (
            supabase
            .table("bot_learning_state")
            .insert({
                "chat_id":
                    database_chat_id,

                "messages_since_learning":
                    0,

                "development_stage":
                    1,

                "personality":
                    "",

                "last_learning_at":
                    utc_now(),

                "learning_enabled":
                    True
            })
            .execute()
        )

        return {
            "chat_id":
                database_chat_id,

            "messages_since_learning":
                0,

            "development_stage":
                1,

            "personality":
                "",

            "last_learning_at":
                utc_now(),

            "learning_enabled":
                True
        }

    except Exception as e:

        print(
            "Learning state error:",
            e,
            flush=True
        )

        return {
            "chat_id":
                database_chat_id,

            "messages_since_learning":
                0,

            "development_stage":
                1,

            "personality":
                "",

            "learning_enabled":
                True
        }


def is_learning_enabled(chat_id):

    return bool(
        get_learning_state(
            chat_id
        ).get(
            "learning_enabled",
            True
        )
    )


def set_learning_enabled(
    chat_id,
    enabled
):

    try:

        (
            supabase
            .table("bot_learning_state")
            .update({
                "learning_enabled":
                    bool(enabled),

                "messages_since_learning":
                    0
            })
            .eq(
                "chat_id",
                db_chat_id(chat_id)
            )
            .execute()
        )

        return True

    except Exception as e:

        notify_admin_error(
            "Learning state update",
            e,
            f"chat={chat_id}"
        )

        return False


# =========================================================
# LEARNING ADMIN COMMANDS
# =========================================================

def handle_learning_control_command(
    chat_id,
    sender_id,
    text
):

    if not is_admin(sender_id):

        return False, None

    low = normalize_text(
        text
    ).lower()

    # OFF

    if (
        low in (
            "бот обучение выключи",
            "бот, обучение выключи",
            "бот выключи обучение",
            "бот, выключи обучение",
            "бот самообучение выключи",
            "бот, самообучение выключи",
            "бот обучение отключи",
            "бот, обучение отключи",
            "бот отключи обучение",
            "бот, отключи обучение",
            "бот отключи самообучение",
            "бот, отключи самообучение"
        )
    ):

        if set_learning_enabled(
            chat_id,
            False
        ):

            return True, (
                "🛑 Автообучение выключено.\n\n"
                "Старая память НЕ удалена.\n"
                "Обычный чат продолжает работать.\n"
                "Официальная память продолжает работать.\n"
                "Ручное «Запомни...» продолжает работать.\n\n"
                "Включить обратно:\n"
                "бот обучение включи"
            )

        return True, (
            "❌ Не удалось выключить обучение."
        )

    # ON

    if (
        low in (
            "бот обучение включи",
            "бот, обучение включи",
            "бот включи обучение",
            "бот, включи обучение",
            "бот обучение включи обратно",
            "бот, обучение включи обратно",
            "бот самообучение включи",
            "бот, самообучение включи",
            "бот возобнови обучение",
            "бот, возобнови обучение"
        )
    ):

        if set_learning_enabled(
            chat_id,
            True
        ):

            return True, (
                "🟢 Автообучение снова включено."
            )

        return True, (
            "❌ Не удалось включить обучение."
        )

    # STATUS

    if low in (
        "бот статус обучения",
        "бот, статус обучения",
        "бот статус самообучения",
        "бот, статус самообучения",
        "бот обучение статус",
        "бот, обучение статус"
    ):

        state = get_learning_state(
            chat_id
        )

        enabled = bool(
            state.get(
                "learning_enabled",
                True
            )
        )

        counter = int(
            state.get(
                "messages_since_learning",
                0
            )
        )

        stage = int(
            state.get(
                "development_stage",
                1
            )
        )

        return True, (
            "🧠 СТАТУС ОБУЧЕНИЯ\n\n"
            f"Состояние: "
            f"{'🟢 ВКЛ' if enabled else '🛑 ВЫКЛ'}\n"
            f"Счётчик: {counter}/"
            f"{LEARNING_EVERY_MESSAGES}\n"
            f"Стадия: {stage}"
        )

    return False, None


# =========================================================
# LEARNING COUNTER
# =========================================================

def increase_learning_counter(chat_id):

    state = get_learning_state(
        chat_id
    )

    previous = int(
        state.get(
            "messages_since_learning",
            0
        )
    )

    count = min(
        previous + 1,
        LEARNING_EVERY_MESSAGES
    )

    try:

        (
            supabase
            .table("bot_learning_state")
            .update({
                "messages_since_learning":
                    count
            })
            .eq(
                "chat_id",
                db_chat_id(chat_id)
            )
            .execute()
        )

    except Exception as e:

        print(
            "Learning counter error:",
            e,
            flush=True
        )

    return count


def reset_learning_counter(chat_id):

    try:

        (
            supabase
            .table("bot_learning_state")
            .update({
                "messages_since_learning":
                    0
            })
            .eq(
                "chat_id",
                db_chat_id(chat_id)
            )
            .execute()
        )

        return True

    except Exception as e:

        notify_admin_error(
            "Learning counter reset",
            e,
            f"chat={chat_id}"
        )

        return False


# =========================================================
# ADMIN STATISTICS
# =========================================================

def admin_memory_stats(chat_id):

    users = 0
    messages = 0
    knowledge = 0
    official = 0

    try:

        result = (
            supabase
            .table("bot_users")
            .select(
                "id",
                count="exact",
                head=True
            )
            .eq(
                "chat_id",
                db_chat_id(chat_id)
            )
            .execute()
        )

        users = int(
            result.count or 0
        )

    except Exception:
        pass

    try:

        messages = get_chat_message_count(
            chat_id
        )

    except Exception:
        pass

    try:

        result = (
            supabase
            .table("bot_knowledge")
            .select(
                "id",
                count="exact",
                head=True
            )
            .eq(
                "chat_id",
                db_chat_id(chat_id)
            )
            .execute()
        )

        knowledge = int(
            result.count or 0
        )

    except Exception:
        pass

    try:

        result = (
            supabase
            .table("bot_official_knowledge")
            .select(
                "id",
                count="exact",
                head=True
            )
            .eq(
                "chat_id",
                db_chat_id(chat_id)
            )
            .execute()
        )

        official = int(
            result.count or 0
        )

    except Exception:
        pass

    state = get_learning_state(
        chat_id
    )

    return (
        "📊 СТАТИСТИКА БОТА\n\n"
        f"👥 Пользователей в памяти: {users}\n"
        f"💬 Сообщений в истории: {messages}\n"
        f"🧠 Автознаний: {knowledge}\n"
        f"📚 Официальных записей: {official}\n"
        f"🧠 Обучение: "
        f"{'🟢 включено' if state.get('learning_enabled', True) else '🛑 выключено'}\n"
        f"📈 Счётчик: "
        f"{state.get('messages_since_learning', 0)}/"
        f"{LEARNING_EVERY_MESSAGES}\n"
        f"🎓 Стадия: "
        f"{state.get('development_stage', 1)}\n"
        f"🤖 Версия: {BOT_VERSION}"
    )


def admin_knowledge_text(chat_id):

    rows = get_all_knowledge(
        chat_id,
        50
    )

    if not rows:

        return (
            "🧠 Автоматических знаний пока нет."
        )

    lines = [
        "🧠 АВТОМАТИЧЕСКИЕ ЗНАНИЯ:",
        ""
    ]

    for index, row in enumerate(
        rows,
        start=1
    ):

        kid = row.get(
            "id"
        )

        knowledge = normalize_text(
            row.get(
                "knowledge"
            )
            or ""
        )

        importance = row.get(
            "importance",
            1
        )

        if len(knowledge) > 250:
            knowledge = knowledge[:250] + "..."

        lines.append(
            f"{index}. ID {kid} | "
            f"важность {importance}"
        )

        lines.append(
            f"   {knowledge}"
        )

    return "\n".join(lines)


def admin_help_text():

    return (
        "👑 АДМИН-ПАНЕЛЬ V1.3\n\n"

        "🧠 ОБУЧЕНИЕ\n"
        "бот обучение включи\n"
        "бот обучение выключи\n"
        "бот статус обучения\n"
        "бот сброс обучения\n\n"

        "📊 ПАМЯТЬ\n"
        "бот память\n"
        "бот знания\n"
        "бот знания удалить ID\n"
        "бот знания очистить\n\n"

        "📚 ОФИЦИАЛЬНАЯ ПАМЯТЬ\n"
        "!tbadd название | текст\n"
        "!tbupdate название | текст\n"
        "!tbdelete название\n"
        "!tbmemory\n"
        "!tbhelp\n\n"

        "🤖 РЕЖИМ БОТА\n"
        "бот ответы выключи\n"
        "бот ответы включи\n"
        "бот статус\n\n"

        "🧪 ПРОЧЕЕ\n"
        "бот тестеры\n"
        "бот версия\n"
        "бот админ помощь\n\n"

        "🔐 Все команды доступны только администрации."
    )


def handle_admin_command(
    chat_id,
    sender_id,
    text
):

    if not is_admin(sender_id):

        return False, None

    raw = normalize_text(
        text
    )

    low = raw.lower()

    if low in (
        "бот админ помощь",
        "бот, админ помощь",
        "бот помощь админ",
        "бот, помощь админ",
        "!adminhelp"
    ):

        return True, admin_help_text()

    if low in (
        "бот память",
        "бот, память",
        "!memory"
    ):

        return True, admin_memory_stats(
            chat_id
        )

    if low in (
        "бот знания",
        "бот, знания",
        "бот показать знания",
        "бот, показать знания",
        "!knowledge"
    ):

        return True, admin_knowledge_text(
            chat_id
        )

    if low.startswith(
        "бот знания удалить "
    ):

        value = raw[
            len("бот знания удалить "):
        ].strip()

        try:
            kid = int(value)

        except Exception:

            return True, (
                "❌ Укажи ID знания.\n"
                "Например: бот знания удалить 15"
            )

        if delete_knowledge_by_id(
            chat_id,
            kid
        ):

            return True, (
                f"🗑 Знание ID {kid} удалено."
            )

        return True, (
            "❌ Не удалось удалить знание."
        )

    if low in (
        "бот знания очистить",
        "бот, знания очистить"
    ):

        return True, (
            "⚠️ Для очистки всех автоматических "
            "знаний используй:\n"
            "бот знания очистить ПОДТВЕРЖДАЮ"
        )

    if low in (
        "бот знания очистить подтверждаю",
        "бот, знания очистить подтверждаю"
    ):

        if clear_knowledge(chat_id):

            return True, (
                "🧹 Все автоматически полученные "
                "знания этого чата удалены.\n"
                "Официальная память НЕ затронута."
            )

        return True, (
            "❌ Не удалось очистить знания."
        )

    if low in (
        "бот сброс обучения",
        "бот, сброс обучения",
        "бот сбросить обучение",
        "бот, сбросить обучение"
    ):

        if reset_learning_counter(
            chat_id
        ):

            return True, (
                "🔄 Счётчик самообучения сброшен.\n"
                "Сама память сохранена."
            )

        return True, (
            "❌ Не удалось сбросить счётчик."
        )

    if low in (
        "бот ответы выключи",
        "бот, ответы выключи",
        "бот отключи ответы",
        "бот, отключи ответы"
    ):

        set_response_enabled(
            chat_id,
            False
        )

        return True, (
            "🔇 Обычные ответы бота выключены "
            "в этом чате.\n"
            "Память и обучение продолжают работать."
        )

    if low in (
        "бот ответы включи",
        "бот, ответы включи",
        "бот включи ответы",
        "бот, включи ответы"
    ):

        set_response_enabled(
            chat_id,
            True
        )

        return True, (
            "🔊 Обычные ответы бота снова включены."
        )

    if low in (
        "бот статус",
        "бот, статус"
    ):

        state = get_learning_state(
            chat_id
        )

        return True, (
            "🤖 СТАТУС БОТА\n\n"
            f"Версия: {BOT_VERSION}\n"
            f"Ответы: "
            f"{'🟢 включены' if is_response_enabled(chat_id) else '🔇 выключены'}\n"
            f"Обучение: "
            f"{'🟢 включено' if state.get('learning_enabled', True) else '🛑 выключено'}\n"
            f"Защита участников: 🛡 включена\n"
            f"Официальная память: 📚 включена\n"
            f"Тестеров: {len(TESTER_IDS)}\n"
            f"Основная модель: {MAIN_MODEL}\n"
            f"Резервная модель: {BACKUP_MODEL}\n"
            f"OpenRouter: "
            f"{'🟢 есть ключ' if OPENROUTER_API_KEY else '🔴 нет ключа'}"
        )

    if low in (
        "бот версия",
        "бот, версия",
        "!version"
    ):

        return True, (
            f"🤖 Tanks Blitz AI\n"
            f"Версия: {BOT_VERSION}\n"
            f"Сборка: {BOT_BUILD}"
        )

    if low in (
        "бот тестеры",
        "бот, тестеры"
    ):

        ids = sorted(
            TESTER_IDS
        )

        return True, (
            "🧪 ТЕСТЕРЫ:\n\n"
            +
            "\n".join(
                f"• {uid}"
                for uid in ids
            )
        )

    return False, None


# =========================================================
# TEXT CLEANER
# =========================================================

def clean_model_text(text):

    if not text:
        return ""

    text = re.sub(
        r"<think>.*?</think>",
        "",
        str(text),
        flags=re.DOTALL | re.IGNORECASE
    )

    text = re.sub(
        r"<think>.*$",
        "",
        text,
        flags=re.DOTALL | re.IGNORECASE
    )

    return text.strip()


# =========================================================
# GROQ
# =========================================================

def ask_model(
    model,
    messages,
    max_tokens=GROQ_MAX_TOKENS
):

    try:

        completion = (
            groq.chat.completions.create(
                model=model,
                messages=messages,
                max_completion_tokens=max_tokens,
                reasoning_effort="low",
                reasoning_format="hidden"
            )
        )

    except Exception:

        completion = (
            groq.chat.completions.create(
                model=model,
                messages=messages,
                max_tokens=max_tokens,
                reasoning_effort="low"
            )
        )

    if not completion.choices:

        raise RuntimeError(
            "Groq returned no choices."
        )

    message = completion.choices[0].message

    reply = clean_model_text(
        getattr(
            message,
            "content",
            None
        )
        or ""
    )

    if not reply:

        raise RuntimeError(
            "Groq returned empty response."
        )

    return reply


# =========================================================
# OPENROUTER
# =========================================================

def ask_openrouter_messages(
    messages,
    max_tokens=OPENROUTER_MAX_TOKENS,
    label="OpenRouter"
):

    if not OPENROUTER_API_KEY:

        raise RuntimeError(
            "OPENROUTER_API_KEY не установлен."
        )

    response = requests.post(
        OPENROUTER_API,
        headers={
            "Authorization":
                f"Bearer {OPENROUTER_API_KEY}",

            "Content-Type":
                "application/json",

            "HTTP-Referer":
                "https://vk-bot-1-khev.onrender.com",

            "X-Title":
                "Tanks Blitz AI"
        },
        json={
            "model":
                OPENROUTER_MODEL,

            "messages":
                messages,

            "max_tokens":
                max_tokens,

            "stream":
                False
        },
        timeout=60
    )

    if response.status_code != 200:

        raise RuntimeError(
            f"{label} HTTP "
            f"{response.status_code}: "
            f"{response.text[:1000]}"
        )

    data = response.json()

    if data.get("error"):

        raise RuntimeError(
            f"{label}: "
            f"{data.get('error')}"
        )

    choices = data.get(
        "choices"
    ) or []

    if not choices:

        raise RuntimeError(
            f"{label} returned no choices."
        )

    content = (
        choices[0]
        .get("message", {})
        .get("content")
    )

    if isinstance(content, list):

        content = "\n".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict)
            and part.get("type") == "text"
        )

    reply = clean_model_text(
        content or ""
    )

    if not reply:

        raise RuntimeError(
            f"{label} returned empty response."
        )

    return reply


# =========================================================
# LEARNING MODEL
# =========================================================

def ask_learning_model(messages):

    global main_blocked_until
    global backup_blocked_until

    if time.time() >= backup_blocked_until:

        try:

            return ask_model(
                BACKUP_MODEL,
                messages,
                LEARNING_MAX_TOKENS
            )

        except Exception as e:

            if is_rate_limit_error(e):

                backup_blocked_until = (
                    time.time()
                    +
                    get_retry_seconds(
                        e,
                        600
                    )
                )

            print(
                "Learning 20B error:",
                e,
                flush=True
            )

    if time.time() >= main_blocked_until:

        try:

            return ask_model(
                MAIN_MODEL,
                messages,
                LEARNING_MAX_TOKENS
            )

        except Exception as e:

            if is_rate_limit_error(e):

                main_blocked_until = (
                    time.time()
                    +
                    get_retry_seconds(
                        e,
                        3600
                    )
                )

            print(
                "Learning 120B error:",
                e,
                flush=True
            )

    if OPENROUTER_API_KEY:

        try:

            return ask_openrouter_messages(
                messages,
                LEARNING_MAX_TOKENS,
                "OpenRouter Learning"
            )

        except Exception as e:

            print(
                "OpenRouter learning error:",
                e,
                flush=True
            )

    raise RuntimeError(
        "Все модели обучения временно недоступны."
    )


# =========================================================
# SELF LEARNING
# =========================================================

def perform_learning(chat_id):

    try:

        if not is_allowed_vk_chat(
            chat_id
        ):
            return

        if not is_learning_enabled(
            chat_id
        ):
            return

        history = get_chat_memory(
            chat_id,
            LEARNING_HISTORY_LIMIT
        )

        # -------------------------------------------------
        # НОВЫЙ ФИЛЬТР
        # Только Tanks Blitz
        # -------------------------------------------------

        history = filter_learning_history(
            history
        )

        if len(history) < 5:

            reset_learning_counter(
                chat_id
            )

            print(
                "LEARNING: мало игровых данных",
                flush=True
            )

            return

        text_parts = []

        known_names = {}

        for item in history:

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

            if uid:
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

        prompt = f"""
Ты — модуль долговременного обучения
AI-бота Tanks Blitz.

Тебе разрешено сохранять ТОЛЬКО информацию,
связанную с Tanks Blitz.

Ищи:

- игровые факты;
- ТТХ;
- танки;
- оружие;
- броню;
- пробитие;
- механики;
- карты;
- режимы;
- игровые ситуации;
- устойчивые игровые предпочтения;
- любимые танки;
- игровые привычки;
- сленг сообщества;
- факты о конкретном чате,
  если они явно связаны с Tanks Blitz.

СТРОГО НЕ СОХРАНЯЙ:

- школьные разговоры;
- работу;
- здоровье;
- медицину;
- отношения;
- личную жизнь;
- адреса;
- документы;
- пароли;
- банковские данные;
- политические темы;
- религию;
- срачи;
- оскорбления;
- мемы без игровой пользы;
- случайный флуд;
- эмоции;
- предположения;
- слухи без явного подтверждения;
- выдуманные факты.

ОСОБЕННО ВАЖНО:

Не превращай мнение человека
в официальный игровой факт.

Например:

«мне кажется этот танк имба»

НЕ означает:

«этот танк объективно самый сильный».

Если человек говорит:

«мой любимый танк — E 100»

можно сохранить:

USER|ID|Любимый танк — E 100

Если человек пишет:

«Иван дебил»

НИКОГДА не сохраняй это как факт.

Если информация выглядит сомнительной,
лучше не сохраняй её.

ФОРМАТ:

USER|ID|Факт

или

CHAT|Факт|важность

важность 1–5.

Если полезных данных нет:

NONE

Данные чата:

{chr(10).join(text_parts)}
"""

        learned = ask_learning_model(
            [
                {
                    "role":
                        "system",

                    "content":
                        (
                            "Ты строгий фильтр "
                            "игровых знаний. "
                            "Сохраняй только то, "
                            "что действительно относится "
                            "к Tanks Blitz. "
                            "Ничего не выдумывай."
                        )
                },
                {
                    "role":
                        "user",

                    "content":
                        prompt
                }
            ]
        )

        learned = clean_model_text(
            learned
        )

        if learned.upper() != "NONE":

            for raw in learned.splitlines():

                line = raw.strip()

                if not line:
                    continue

                if line.upper() == "NONE":
                    continue

                if line.startswith(
                    "USER|"
                ):

                    parts = line.split(
                        "|",
                        2
                    )

                    if len(parts) != 3:
                        continue

                    _, uid, fact = parts

                    try:
                        numeric_uid = int(
                            uid.strip()
                        )

                    except Exception:
                        continue

                    fact = fact.strip()

                    if not fact:
                        continue

                    # Дополнительная защита:
                    # пользовательский факт тоже
                    # должен быть игровым.
                    if not is_game_relevant(
                        fact
                    ):
                        continue

                    save_user_memory(
                        chat_id,
                        numeric_uid,
                        known_names.get(
                            str(numeric_uid)
                        ),
                        fact
                    )

                elif line.startswith(
                    "CHAT|"
                ):

                    parts = line.split(
                        "|",
                        2
                    )

                    if len(parts) != 3:
                        continue

                    _, fact, importance = parts

                    fact = fact.strip()

                    if not fact:
                        continue

                    if not is_game_relevant(
                        fact
                    ):
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
                        importance
                    )

        state = get_learning_state(
            chat_id
        )

        stage = int(
            state.get(
                "development_stage",
                1
            )
        )

        total = get_chat_message_count(
            chat_id
        )

        if stage < 2 and total >= 300:
            stage = 2

        if stage < 3 and total >= 1000:
            stage = 3

        if stage < 4 and total >= 3000:
            stage = 4

        (
            supabase
            .table("bot_learning_state")
            .update({
                "messages_since_learning":
                    0,

                "development_stage":
                    stage,

                "last_learning_at":
                    utc_now()
            })
            .eq(
                "chat_id",
                db_chat_id(chat_id)
            )
            .execute()
        )

        learning_retry_until.pop(
            chat_id,
            None
        )

        print(
            f"🧠 LEARNING COMPLETE | "
            f"version={BOT_VERSION} | "
            f"chat={chat_id} | "
            f"stage={stage}",
            flush=True
        )

    except Exception as e:

        learning_retry_until[
            chat_id
        ] = (
            time.time()
            +
            LEARNING_RETRY_TIME
        )

        print(
            "Learning error:",
            e,
            flush=True
        )

        notify_admin_error(
            "Self-learning",
            e,
            f"chat={chat_id}"
        )

    finally:

        with learning_lock:

            learning_running.discard(
                chat_id
            )


def maybe_learn(chat_id):

    if not is_allowed_vk_chat(
        chat_id
    ):
        return

    if not is_learning_enabled(
        chat_id
    ):
        return

    count = increase_learning_counter(
        chat_id
    )

    if count < LEARNING_EVERY_MESSAGES:
        return

    retry_until = (
        learning_retry_until.get(
            chat_id,
            0
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
        daemon=True
    ).start()


# =========================================================
# CHAT CONTEXT
# =========================================================

def build_chat_context(
    chat_id,
    user_id,
    user_name,
    text
):

    messages = [
        {
            "role":
                "system",

            "content":
                SYSTEM_PROMPT
        }
    ]

    state = get_learning_state(
        chat_id
    )

    stage = int(
        state.get(
            "development_stage",
            1
        )
    )

    messages.append({
        "role":
            "system",

        "content":
            (
                "Стадия развития:\n"
                +
                DEVELOPMENT_STAGES.get(
                    stage,
                    DEVELOPMENT_STAGES[1]
                )
            )
    })

    official = get_official_knowledge(
        chat_id,
        OFFICIAL_KNOWLEDGE_LIMIT
    )

    if official:

        lines = []

        for item in official:

            title = (
                item.get("title")
                or ""
            ).strip()

            content = (
                item.get("content")
                or ""
            ).strip()

            if title and content:

                lines.append(
                    f"[{title}] {content}"
                )

        if lines:

            messages.append({
                "role":
                    "system",

                "content":
                    (
                        "=== ОФИЦИАЛЬНЫЕ ДАННЫЕ "
                        "TANKS BLITZ ===\n"
                        +
                        "\n".join(lines)
                        +
                        "\n=== КОНЕЦ ==="
                    )
            })

    knowledge = get_knowledge(
        chat_id
    )

    if knowledge:

        lines = []

        for item in knowledge:

            value = (
                item.get("knowledge")
                or ""
            ).strip()

            if value:
                lines.append(
                    f"- {value}"
                )

        if lines:

            messages.append({
                "role":
                    "system",

                "content":
                    (
                        "=== ИГРОВАЯ ПАМЯТЬ ЧАТА ===\n"
                        +
                        "\n".join(lines)
                        +
                        "\n=== КОНЕЦ ==="
                    )
            })

    history = get_chat_memory(
        chat_id,
        CHAT_MEMORY_LIMIT
    )

    current_saved = False

    for item in history:

        role = item.get(
            "role"
        )

        content = (
            item.get("content")
            or ""
        )

        if not content:
            continue

        name = (
            item.get("speaker_name")
            or "Участник"
        )

        sid = str(
            item.get("speaker_id")
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
                "role":
                    "user",

                "content":
                    f"{name}: {content}"
            })

        elif role == "assistant":

            messages.append({
                "role":
                    "assistant",

                "content":
                    content
            })

    personal = get_user_memory(
        chat_id,
        user_id
    )

    if (
        personal
        and personal.get("memory")
    ):

        memory = (
            personal["memory"]
            or ""
        ).strip()

        if memory:

            messages.append({
                "role":
                    "system",

                "content":
                    (
                        "Личная память текущего "
                        "пользователя:\n"
                        +
                        memory
                    )
            })

    if not current_saved:

        messages.append({
            "role":
                "user",

            "content":
                (
                    f"{user_name or 'Участник'}: "
                    f"{text}"
                )
        })

    return messages


# =========================================================
# DIRECT ADDRESS
# =========================================================

def contains_bot_word(text):

    low = normalize_text(
        text
    ).lower()

    return bool(
        re.search(
            r"(?<!\w)бот(?!\w)",
            low
        )
    )


def is_directed_to_bot_vk(
    message,
    text
):

    low = normalize_text(
        text
    ).lower()

    reply = message.get(
        "reply_message"
    )

    if reply:

        from_id = reply.get(
            "from_id"
        )

        if from_id is not None:

            try:

                # Если ответ идёт на сообщение бота,
                # VK обычно содержит ID сообщества.
                if int(from_id) < 0:
                    return True

            except Exception:
                pass

    # Упоминание сообщества VK
    if re.search(
        r"\[club\d+\|",
        low
    ):
        return True

    # Прямое обращение:
    # "бот, ..."
    # "бот ..."
    # "эй бот ..."
    if re.search(
        r"^(?:эй\s+)?бот\b",
        low
    ):
        return True

    # Обращение в конце:
    # "ты что думаешь, бот?"
    if re.search(
        r"\bбот[!?.,]*$",
        low
    ):
        return True

    return False


def is_directed_to_bot_telegram(
    message,
    text
):

    low = normalize_text(
        text
    ).lower()

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
        and re.search(
            rf"@{re.escape(TELEGRAM_BOT_USERNAME.lower())}\b",
            low
        )
    ):
        return True

    if re.search(
        r"^(?:эй\s+)?бот\b",
        low
    ):
        return True

    if re.search(
        r"\bбот[!?.,]*$",
        low
    ):
        return True

    return False


# =========================================================
# QUESTION / GAME DETECTION
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
    "есть ли"
)


def looks_like_question(text):

    low = normalize_text(
        text
    ).lower()

    if "?" in low:
        return True

    return any(
        re.search(
            rf"^{re.escape(word)}\b",
            low
        )
        for word in QUESTION_WORDS
    )


def is_short_game_message(text):

    return (
        len(text.split()) <= 12
        and is_game_relevant(text)
    )


def should_answer(
    message,
    text,
    platform="vk"
):

    text = normalize_text(
        text
    )

    if not text:
        return False

    if platform == "telegram":

        directed = is_directed_to_bot_telegram(
            message,
            text
        )

    else:

        directed = is_directed_to_bot_vk(
            message,
            text
        )

    # -----------------------------------------------------
    # ПРЯМОЕ ОБРАЩЕНИЕ
    # -----------------------------------------------------

    if directed:
        return True

    # -----------------------------------------------------
    # БЕЗ ОБРАЩЕНИЯ:
    #
    # Не лезем в обычный разговор.
    # Можно отвечать только на явно игровой вопрос,
    # если он выглядит как запрос к боту.
    #
    # Но не на обычные "кто завтра играет?",
    # "а что есть?" и т.п.
    # -----------------------------------------------------

    return False


# =========================================================
# PERSONAL ATTACK FILTER
# =========================================================

INSULT_STEMS = (
    "дебил",
    "идиот",
    "туп",
    "кретин",
    "мудак",
    "долбо",
    "придур",
    "даун",
    "лох",
    "чмош",
    "клоун",
    "твар",
    "убог",
    "никчем",
    "дегенерат",
    "имбецил",
    "осел",
    "дятел",
    "позорник",
    "позор",
)

PERSON_TARGET_WORDS = (
    "ты",
    "тебя",
    "тебе",
    "тобой",
    "он",
    "она",
    "его",
    "ее",
    "этот",
    "эта",
    "тот",
    "та",
    "чел",
    "человек",
    "игрок",
    "игрока",
    "участник",
)


def looks_like_personal_attack(text):

    low = normalize_text(
        text
    ).lower()

    if not low:
        return False

    has_insult = any(
        stem in low
        for stem in INSULT_STEMS
    )

    if not has_insult:
        return False

    return any(
        re.search(
            rf"\b{re.escape(word)}\b",
            low
        )
        for word in PERSON_TARGET_WORDS
    )


# =========================================================
# INTERVENTION AI
# =========================================================

def ask_intervention_model(
    text,
    context
):

    prompt = f"""
Сообщение:

{text}

Контекст:

{context}

Определи, нужно ли AI-участнику
защитить другого участника.

Вмешивайся ТОЛЬКО если есть реальное
личное унижение или оскорбление.

Не вмешивайся в:
- обычный спор;
- критику игры;
- обычный мат;
- шутки между друзьями;
- несогласие;
- игровые подколы без личной атаки.

Если вмешиваться не надо:

NONE

Если надо:
одна короткая реплика.

Стиль:
дерзкий, ироничный, короткий.

Без:
- угроз;
- семьи;
- здоровья;
- внешности;
- защищённых признаков;
- травли.

Максимум 2 предложения.

Если сомневаешься — NONE.
"""

    messages = [
        {
            "role":
                "system",

            "content":
                (
                    "Ты осторожный классификатор "
                    "личных конфликтов."
                )
        },
        {
            "role":
                "user",

            "content":
                prompt
        }
    ]

    try:

        return ask_model(
            BACKUP_MODEL,
            messages,
            INTERVENTION_MAX_TOKENS
        )

    except Exception as e:

        print(
            "Intervention error:",
            e,
            flush=True
        )

        return "NONE"


def maybe_intervene_vk(
    chat_id,
    sender_id,
    user_name,
    text,
    message
):

    if is_admin(sender_id):
        return False

    if not looks_like_personal_attack(
        text
    ):
        return False

    now = time.time()

    with intervention_lock:

        blocked_until = intervention_until.get(
            chat_id,
            0
        )

        if now < blocked_until:
            return False

    history = get_chat_memory(
        chat_id,
        10
    )

    context_lines = []

    for item in history:

        name = (
            item.get("speaker_name")
            or "Участник"
        )

        content = (
            item.get("content")
            or ""
        )

        if content:

            context_lines.append(
                f"{name}: {content}"
            )

    context = "\n".join(
        context_lines
    )

    reply_message = message.get(
        "reply_message"
    )

    if reply_message:

        reply_text = (
            reply_message.get("text")
            or ""
        )

        reply_from = reply_message.get(
            "from_id"
        )

        if reply_text:

            context += (
                "\nОтвет на сообщение:\n"
                f"[ID:{reply_from}] "
                f"{reply_text}"
            )

    response = ask_intervention_model(
        text,
        context
    )

    response = clean_model_text(
        response
    )

    if (
        not response
        or response.upper().startswith("NONE")
    ):
        return False

    with intervention_lock:

        intervention_until[
            chat_id
        ] = (
            time.time()
            + INTERVENTION_COOLDOWN
        )

    save_chat_message(
        chat_id,
        None,
        "Бот",
        "assistant",
        response
    )

    send_message(
        chat_id,
        response
    )

    print(
        f"🛡 INTERVENTION | "
        f"chat={chat_id} | "
        f"user={sender_id}",
        flush=True
    )

    return True


# =========================================================
# GROQ CHAT
# =========================================================

def ask_groq(
    chat_id,
    text,
    user_id,
    user_name
):

    global main_blocked_until
    global backup_blocked_until

    messages = build_chat_context(
        chat_id,
        user_id,
        user_name,
        text
    )

    if time.time() >= main_blocked_until:

        try:

            return ask_model(
                MAIN_MODEL,
                messages,
                GROQ_MAX_TOKENS
            )

        except Exception as e:

            if is_rate_limit_error(e):

                main_blocked_until = (
                    time.time()
                    +
                    get_retry_seconds(
                        e,
                        3600
                    )
                )

            print(
                "120B error:",
                e,
                flush=True
            )

    if time.time() >= backup_blocked_until:

        try:

            return ask_model(
                BACKUP_MODEL,
                messages,
                GROQ_MAX_TOKENS
            )

        except Exception as e:

            if is_rate_limit_error(e):

                backup_blocked_until = (
                    time.time()
                    +
                    get_retry_seconds(
                        e,
                        600
                    )
                )

            print(
                "20B error:",
                e,
                flush=True
            )

    raise RuntimeError(
        "Обе модели Groq временно недоступны."
    )


def ask_openrouter(
    chat_id,
    text,
    user_id,
    user_name
):

    messages = build_chat_context(
        chat_id,
        user_id,
        user_name,
        text
    )

    return ask_openrouter_messages(
        messages,
        OPENROUTER_MAX_TOKENS,
        "OpenRouter"
    )


def ask_ai(
    chat_id,
    text,
    user_id,
    user_name
):

    try:

        return ask_groq(
            chat_id,
            text,
            user_id,
            user_name
        )

    except Exception as groq_error:

        print(
            "Groq final error:",
            groq_error,
            flush=True
        )

        if OPENROUTER_API_KEY:

            try:

                return ask_openrouter(
                    chat_id,
                    text,
                    user_id,
                    user_name
                )

            except Exception as openrouter_error:

                print(
                    "OpenRouter final error:",
                    openrouter_error,
                    flush=True
                )

                notify_admin_error(
                    "AI final failure",
                    openrouter_error,
                    f"chat={chat_id} user={user_id}"
                )

        raise RuntimeError(
            "Все текстовые AI "
            "временно недоступны."
        )


# =========================================================
# VK SEND
# =========================================================

def send_message(
    peer_id,
    text
):

    if not text:
        return None

    if not is_allowed_vk_chat(
        peer_id
    ):
        return None

    try:

        response = requests.post(
            f"{VK_API}/messages.send",
            data={
                "access_token": VK_TOKEN,
                "v": VK_VERSION,
                "peer_id": int(peer_id),
                "message": text[:4096],
                "random_id": random.randint(
                    1,
                    2147483647
                )
            },
            timeout=15
        )

        result = response.json()

        if "error" in result:

            notify_admin_error(
                "VK messages.send",
                result["error"],
                f"peer_id={peer_id}"
            )

        return result

    except Exception as e:

        notify_admin_error(
            "VK send exception",
            e,
            f"peer_id={peer_id}"
        )

        return None


# =========================================================
# TELEGRAM
# =========================================================

def telegram_call(
    method,
    **kwargs
):

    if not TELEGRAM_API:

        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN не установлен"
        )

    response = requests.post(
        f"{TELEGRAM_API}/{method}",
        json=kwargs,
        timeout=30
    )

    data = response.json()

    if not data.get("ok"):

        raise RuntimeError(
            f"Telegram {method}: {data}"
        )

    return data.get("result")


def send_telegram_message(
    chat_id,
    text,
    reply_to_message_id=None
):

    if not text:
        return

    payload = {
        "chat_id":
            int(chat_id),

        "text":
            text[:4096],

        "disable_web_page_preview":
            True
    }

    if reply_to_message_id:

        payload["reply_parameters"] = {
            "message_id":
                int(reply_to_message_id)
        }

    return telegram_call(
        "sendMessage",
        **payload
    )


# =========================================================
# ACTIVE CHATS
# =========================================================

def register_active_chat(
    platform,
    peer_id
):

    if platform == "vk":

        if not is_allowed_vk_chat(
            peer_id
        ):
            return

    key = (
        f"{platform}:{peer_id}"
    )

    with activity_lock:

        active_chats[key] = {
            "platform":
                platform,

            "peer_id":
                str(peer_id),

            "last":
                time.time()
        }


# =========================================================
# ACTIVITY LOOP
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

                platform = item["platform"]

                peer_id = item["peer_id"]

                if platform == "vk":

                    if not is_allowed_vk_chat(
                        peer_id
                    ):
                        continue

                # -------------------------------------------------
                # ВАЖНО:
                # Спонтанные сообщения теперь сильно ограничены.
                # -------------------------------------------------

                if (
                    now - item["last"]
                    < 30 * 60
                ):
                    continue

                with activity_lock:

                    if key in active_chats:

                        active_chats[
                            key
                        ]["last"] = now

                # Вероятность маленькая.
                if random.random() > 0.08:
                    continue

                # Если обычные ответы отключены,
                # спонтанное сообщение тоже запрещено.
                if platform == "vk":

                    if not is_response_enabled(
                        int(peer_id)
                    ):
                        continue

                prompt = (
                    "В чате давно тихо. "
                    "Если действительно можно "
                    "органично оживить разговор, "
                    "создай ОДНУ короткую фразу "
                    "участника Tanks Blitz. "
                    "Не придумывай новости, "
                    "ТТХ или факты. "
                    "Если нечего сказать — ответь NONE."
                )

                try:

                    reply = ask_groq(
                        int(peer_id),
                        prompt,
                        None,
                        None
                    )

                    reply = clean_model_text(
                        reply
                    )

                    if (
                        not reply
                        or reply.upper().startswith("NONE")
                    ):
                        continue

                    if platform == "vk":

                        send_message(
                            int(peer_id),
                            reply
                        )

                    else:

                        send_telegram_message(
                            int(peer_id),
                            reply
                        )

                    save_chat_message(
                        int(peer_id),
                        None,
                        "Бот",
                        "assistant",
                        reply
                    )

                except Exception as e:

                    print(
                        "Activity error:",
                        e,
                        flush=True
                    )

            time.sleep(60)

        except Exception as e:

            print(
                "Activity loop error:",
                e,
                flush=True
            )

            time.sleep(60)


# =========================================================
# RENDER
# =========================================================

@app.route(
    "/",
    methods=["GET"]
)
def home():

    learning_status = None

    try:

        if ALLOWED_VK_PEER_ID:

            learning_status = is_learning_enabled(
                ALLOWED_VK_PEER_ID
            )

    except Exception:

        learning_status = None

    return {
        "status":
            "ok",

        "bot":
            "Tanks Blitz AI",

        "version":
            BOT_VERSION,

        "build":
            BOT_BUILD,

        "self_learning":
            True,

        "learning_control":
            True,

        "learning_enabled":
            learning_status,

        "official_tanks_blitz_memory":
            True,

        "participant_defense":
            True,

        "admin_controls":
            True,

        "openrouter":
            bool(
                OPENROUTER_API_KEY
            ),

        "vk_group_only":
            True,

        "vk_allowed_peer_id":
            ALLOWED_VK_PEER_ID,

        "vision":
            False,

        "voice":
            False
    }, 200


# =========================================================
# VK CALLBACK
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

        if not isinstance(
            data,
            dict
        ):
            return "ok"

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
            ""
        )

        if already_processed(
            "vk:" + str(event_id)
        ):
            return "ok"

        message = (
            data.get(
                "object",
                {}
            ).get(
                "message",
                {}
            )
        )

        if not message:
            return "ok"

        peer_id = message.get(
            "peer_id"
        )

        sender_id = (
            message.get("from_id")
            or
            message.get("user_id")
        )

        if (
            peer_id is None
            or sender_id is None
        ):
            return "ok"

        if not is_allowed_vk_chat(
            peer_id
        ):
            return "ok"

        if int(peer_id) == int(sender_id):
            return "ok"

        chat_id = int(peer_id)

        register_active_chat(
            "vk",
            peer_id
        )

        text = (
            message.get("text")
            or ""
        ).strip()

        if not text:
            return "ok"

        user_name = get_vk_user_name(
            sender_id
        )

        # =====================================================
        # ADMIN COMMANDS
        # =====================================================

        handled, reply = handle_admin_tb_command(
            chat_id,
            sender_id,
            text
        )

        if handled:

            if reply:

                send_message(
                    peer_id,
                    reply
                )

            return "ok"

        handled, reply = handle_learning_control_command(
            chat_id,
            sender_id,
            text
        )

        if handled:

            if reply:

                send_message(
                    peer_id,
                    reply
                )

            return "ok"

        handled, reply = handle_admin_command(
            chat_id,
            sender_id,
            text
        )

        if handled:

            if reply:

                send_message(
                    peer_id,
                    reply
                )

            return "ok"

        # =====================================================
        # SAVE MESSAGE
        # =====================================================

        save_chat_message(
            chat_id,
            sender_id,
            user_name,
            "user",
            text
        )

        # =====================================================
        # EXPLICIT MEMORY
        # =====================================================

        save_explicit_user_memory(
            chat_id,
            sender_id,
            user_name,
            text
        )

        # =====================================================
        # LEARNING
        # =====================================================

        maybe_learn(
            chat_id
        )

        # =====================================================
        # PARTICIPANT DEFENSE
        # =====================================================

        if maybe_intervene_vk(
            chat_id,
            sender_id,
            user_name,
            text,
            message
        ):
            return "ok"

        # =====================================================
        # RESPONSE SWITCH
        # =====================================================

        if not is_response_enabled(
            chat_id
        ):
            return "ok"

        # =====================================================
        # SHOULD ANSWER
        # =====================================================

        if not should_answer(
            message,
            text,
            "vk"
        ):
            return "ok"

        # =====================================================
        # AI
        # =====================================================

        reply = ask_ai(
            chat_id,
            text,
            str(sender_id),
            user_name
        )

        if reply:

            save_chat_message(
                chat_id,
                None,
                "Бот",
                "assistant",
                reply
            )

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

        notify_admin_error(
            "VK Callback",
            e
        )

        return "ok"


# =========================================================
# TELEGRAM WEBHOOK
# =========================================================

@app.route(
    "/telegram/webhook/<secret>",
    methods=["POST"]
)
def telegram_webhook(
    secret
):

    if not TELEGRAM_BOT_TOKEN:
        return "ok"

    expected = hashlib.sha256(
        TELEGRAM_BOT_TOKEN.encode()
    ).hexdigest()[:32]

    if secret != expected:

        return "forbidden", 403

    try:

        data = (
            request.get_json(
                force=True
            )
            or {}
        )

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

        if sender.get("is_bot"):
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
            raw_chat_id
        )

        user_name = get_telegram_user_name(
            sender
        )

        text = (
            message.get("text")
            or message.get("caption")
            or ""
        ).strip()

        if not text:
            return "ok"

        # =====================================================
        # ADMIN
        # =====================================================

        handled, reply = handle_learning_control_command(
            chat_id,
            sender_id,
            text
        )

        if handled:

            if reply:

                send_telegram_message(
                    raw_chat_id,
                    reply,
                    message.get("message_id")
                )

            return "ok"

        handled, reply = handle_admin_command(
            chat_id,
            sender_id,
            text
        )

        if handled:

            if reply:

                send_telegram_message(
                    raw_chat_id,
                    reply,
                    message.get("message_id")
                )

            return "ok"

        # =====================================================
        # SAVE
        # =====================================================

        save_chat_message(
            chat_id,
            sender_id,
            user_name,
            "user",
            text
        )

        save_explicit_user_memory(
            chat_id,
            sender_id,
            user_name,
            text
        )

        if not is_response_enabled(
            chat_id
        ):
            return "ok"

        if not should_answer(
            message,
            text,
            "telegram"
        ):
            return "ok"

        reply = ask_ai(
            chat_id,
            text,
            str(sender_id),
            user_name
        )

        if reply:

            save_chat_message(
                chat_id,
                None,
                "Бот",
                "assistant",
                reply
            )

            send_telegram_message(
                raw_chat_id,
                reply,
                message.get("message_id")
            )

        return "ok"

    except Exception as e:

        print(
            "Telegram webhook error:",
            e,
            flush=True
        )

        notify_admin_error(
            "Telegram webhook",
            e
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
                ""
            )
        )

        external = (
            os.environ
            .get(
                "RENDER_EXTERNAL_URL",
                ""
            )
            .strip()
            .rstrip("/")
        )

        if not external:

            host = (
                os.environ
                .get(
                    "RENDER_EXTERNAL_HOSTNAME",
                    ""
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
                "Telegram webhook URL не найден.",
                flush=True
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
            drop_pending_updates=False
        )

        print(
            f"Telegram connected: "
            f"@{TELEGRAM_BOT_USERNAME}",
            flush=True
        )

    except Exception as e:

        print(
            "Telegram setup error:",
            e,
            flush=True
        )

        notify_admin_error(
            "Telegram setup",
            e
        )


# =========================================================
# START
# =========================================================

if __name__ == "__main__":

    print(
        "========================================",
        flush=True
    )

    print(
        f"🤖 BOT VERSION: {BOT_VERSION}",
        flush=True
    )

    print(
        f"🧠 BUILD: {BOT_BUILD}",
        flush=True
    )

    print(
        f"🧠 MAIN MODEL: {MAIN_MODEL}",
        flush=True
    )

    print(
        f"🔄 BACKUP MODEL: {BACKUP_MODEL}",
        flush=True
    )

    print(
        f"🆓 OPENROUTER: {OPENROUTER_MODEL}",
        flush=True
    )

    print(
        "🧠 Self-learning: ADMIN CONTROLLED",
        flush=True
    )

    print(
        "🛡 Participant defense: ENABLED",
        flush=True
    )

    print(
        "📚 Official Tanks Blitz memory: ENABLED",
        flush=True
    )

    print(
        "👑 Admin panel: ENABLED",
        flush=True
    )

    print(
        f"👑 Admin ID: {ADMIN_ID}",
        flush=True
    )

    print(
        f"🧪 Testers: {len(TESTER_IDS)}",
        flush=True
    )

    print(
        f"🎯 VK peer: "
        f"{ALLOWED_VK_PEER_ID or 'ALL GROUP CHATS'}",
        flush=True
    )

    print(
        f"🧠 Learning every: "
        f"{LEARNING_EVERY_MESSAGES}",
        flush=True
    )

    print(
        f"💬 Chat memory: "
        f"{CHAT_MEMORY_LIMIT}",
        flush=True
    )

    print(
        "========================================",
        flush=True
    )

    if TELEGRAM_BOT_TOKEN:
        setup_telegram()

    threading.Thread(
        target=activity_loop,
        daemon=True
    ).start()

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
