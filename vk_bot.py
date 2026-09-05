import os
import re
import time
import hashlib
import json
import base64
import random
import subprocess
import threading
from datetime import datetime, timezone

import requests
from flask import Flask, request
from groq import Groq
from supabase import create_client


# =========================================================
# CONFIG
# =========================================================

BOT_VERSION = "V1.3.5"

BOT_BUILD = (
    "Умное самообучение + "
    "админ-панель + "
    "полная модерация + "
    "защита участников + "
    "официальная память Tanks Blitz + "
    "VK + Telegram + OpenRouter + developer mode"
)


# =========================================================
# ADMIN
# =========================================================

ADMIN_ID = 948950706

ADMIN_IDS = {
    ADMIN_ID
}

TESTER_IDS = {
    1020077553
}

SENIOR_MODERATOR_IDS = set()
JUNIOR_MODERATOR_IDS = set(TESTER_IDS)
PROTECTED_IDS = set(ADMIN_IDS) | set(SENIOR_MODERATOR_IDS) | set(JUNIOR_MODERATOR_IDS)

DEVELOPER_ENABLED = True
DEVELOPER_REPO = os.environ.get("GITHUB_REPO", "").strip()
DEVELOPER_BRANCH = os.environ.get("GITHUB_BRANCH", "main").strip()
DEVELOPER_TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()
developer_patch_lock = threading.Lock()
developer_pending_patch = {}

ADMIN_NICK = "Blitz"

INTERVENTION_COOLDOWN = 90

ADMIN_ERROR_COOLDOWN = 300


# =========================================================
# MODERATION CONFIG
# =========================================================

MODERATION_ENABLED_DEFAULT = True

MODERATION_MODEL = BACKUP_MODEL if "BACKUP_MODEL" in globals() else "openai/gpt-oss-20b"

MODERATION_MAX_TOKENS = 260
MODERATION_CONFIDENCE_THRESHOLD = 0.82

WARNING_MUTE_THRESHOLD = 2
WARNING_LONG_MUTE_THRESHOLD = 3
WARNING_KICK_THRESHOLD = None
WARNING_BAN_THRESHOLD = None

MUTE_FIRST_MINUTES = 10
MUTE_SECOND_MINUTES = 30
MUTE_THIRD_MINUTES = 60

MODERATOR_ALERT_COOLDOWN = 60
ADMIN_ALERT_COOLDOWN = 30
MODERATION_ADMIN_NOTIFY_COOLDOWN = 60

# Не модерируем сообщения короче этого значения,
# если они не содержат очевидной угрозы/спама.
MODERATION_MIN_TEXT_LENGTH = 3


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

MODERATION_MODEL = BACKUP_MODEL


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

response_disabled = set()

response_mode_lock = threading.Lock()

moderation_notify_until = {}

moderation_notify_lock = threading.Lock()


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

    low = normalize_text(text).lower()

    if not low:
        return False

    return any(
        keyword in low
        for keyword in GAME_KEYWORDS
    )


def filter_learning_history(history):

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
# GLOBAL SETTINGS / ROLES
# =========================================================

SETTING_DEFAULTS = {
    "moderation_enabled": True,
    "moderation_test_mode": False,
    "warning_enabled": True,
    "mute_enabled": True,
    "mute_2_duration": 10,
    "mute_3_duration": 30,
    "mute_4_duration": 60,
    "confidence_threshold": MODERATION_CONFIDENCE_THRESHOLD,
    "admin_alert_cooldown": ADMIN_ALERT_COOLDOWN,
    "moderator_alert_cooldown": MODERATOR_ALERT_COOLDOWN,
    "admin_notifications": True,
    "moderator_notifications": True,
    "tester_notifications": True,
    "learning_enabled": True,
    "response_enabled": True
}


def _setting_value(raw, default):
    if raw is None:
        return default
    if isinstance(default, bool):
        return str(raw).lower() in ("1", "true", "yes", "on", "вкл")
    if isinstance(default, int):
        try:
            return int(raw)
        except Exception:
            return default
    if isinstance(default, float):
        try:
            return float(raw)
        except Exception:
            return default
    return raw


def get_bot_setting(key):
    default = SETTING_DEFAULTS.get(key)
    try:
        result = supabase.table("bot_settings").select("value").eq("key", key).limit(1).execute()
        if result.data:
            return _setting_value(result.data[0].get("value"), default)
    except Exception as e:
        print("[V1.3.5] [ADMIN] Settings read error:", e, flush=True)
    return default


def set_bot_setting(key, value, updated_by=None):
    try:
        payload = {
            "key": key,
            "value": str(value).lower() if isinstance(value, bool) else str(value),
            "updated_at": utc_now(),
            "updated_by": int(updated_by) if updated_by is not None else ADMIN_ID
        }
        existing = supabase.table("bot_settings").select("key").eq("key", key).limit(1).execute()
        if existing.data:
            supabase.table("bot_settings").update(payload).eq("key", key).execute()
        else:
            supabase.table("bot_settings").insert(payload).execute()
        return True
    except Exception as e:
        print("[V1.3.5] [ADMIN] Settings write error:", e, flush=True)
        return False


def is_senior_moderator(user_id):
    try:
        return int(user_id) in SENIOR_MODERATOR_IDS
    except Exception:
        return False


def is_junior_moderator(user_id):
    try:
        return int(user_id) in JUNIOR_MODERATOR_IDS
    except Exception:
        return False


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


def clear_knowledge(chat_id):

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
# =========================================================
# MODERATION DATABASE
# =========================================================
# =========================================================

def get_moderation_state(
    chat_id,
    user_id
):

    try:

        result = (
            supabase
            .table("bot_moderation_users")
            .select(
                "id, chat_id, user_id, "
                "warnings, muted_until, banned, "
                "updated_at"
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

        if result.data:
            return result.data[0]

    except Exception as e:

        print(
            "Moderation state load error:",
            e,
            flush=True
        )

    return {
        "warnings": 0,
        "muted_until": None,
        "banned": False
    }


def ensure_moderation_state(
    chat_id,
    user_id
):

    state = get_moderation_state(
        chat_id,
        user_id
    )

    if state.get("id"):
        return state

    try:

        result = (
            supabase
            .table("bot_moderation_users")
            .insert({
                "chat_id":
                    db_chat_id(chat_id),

                "user_id":
                    db_user_id(user_id),

                "warnings":
                    0,

                "muted_until":
                    None,

                "banned":
                    False,

                "updated_at":
                    utc_now()
            })
            .execute()
        )

        if result.data:
            return result.data[0]

    except Exception as e:

        print(
            "Moderation state create error:",
            e,
            flush=True
        )

    return state


def update_moderation_state(
    chat_id,
    user_id,
    **fields
):

    try:

        fields["updated_at"] = utc_now()

        existing = ensure_moderation_state(
            chat_id,
            user_id
        )

        if existing.get("id"):

            (
                supabase
                .table("bot_moderation_users")
                .update(fields)
                .eq(
                    "id",
                    existing["id"]
                )
                .execute()
            )

        else:

            fields.update({
                "chat_id":
                    db_chat_id(chat_id),

                "user_id":
                    db_user_id(user_id)
            })

            (
                supabase
                .table("bot_moderation_users")
                .insert(fields)
                .execute()
            )

        return True

    except Exception as e:

        notify_admin_error(
            "Moderation state update",
            e,
            f"chat={chat_id} user={user_id}"
        )

        return False


def get_warning_count(
    chat_id,
    user_id
):

    state = get_moderation_state(
        chat_id,
        user_id
    )

    try:
        return int(
            state.get(
                "warnings",
                0
            )
        )

    except Exception:
        return 0


def add_warning(
    chat_id,
    user_id,
    user_name,
    reason,
    message_text,
    severity="medium"
):

    current = get_warning_count(
        chat_id,
        user_id
    )

    new_count = current + 1

    update_moderation_state(
        chat_id,
        user_id,
        warnings=new_count
    )

    try:

        (
            supabase
            .table("bot_moderation_logs")
            .insert({
                "chat_id":
                    db_chat_id(chat_id),

                "user_id":
                    db_user_id(user_id),

                "user_name":
                    user_name or "",

                "action":
                    "warning",

                "reason":
                    reason[:1000],

                "severity":
                    severity,

                "message_text":
                    message_text[:4000],

                "warnings_after":
                    new_count,

                "created_at":
                    utc_now()
            })
            .execute()
        )

    except Exception as e:

        print(
            "Moderation log warning error:",
            e,
            flush=True
        )

    return new_count


def get_user_moderation_logs(
    chat_id,
    user_id,
    limit=10
):

    try:

        result = (
            supabase
            .table("bot_moderation_logs")
            .select(
                "action, reason, severity, "
                "message_text, warnings_after, "
                "created_at"
            )
            .eq(
                "chat_id",
                db_chat_id(chat_id)
            )
            .eq(
                "user_id",
                db_user_id(user_id)
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
            "Moderation logs error:",
            e,
            flush=True
        )

        return []


def clear_user_warnings(
    chat_id,
    user_id
):

    try:

        update_moderation_state(
            chat_id,
            user_id,
            warnings=0
        )

        return True

    except Exception as e:

        notify_admin_error(
            "Clear warnings",
            e,
            f"chat={chat_id} user={user_id}"
        )

        return False


# =========================================================
# MODERATION SETTINGS
# =========================================================

def get_moderation_enabled(chat_id):

    try:

        result = (
            supabase
            .table("bot_moderation_settings")
            .select(
                "enabled"
            )
            .eq(
                "chat_id",
                db_chat_id(chat_id)
            )
            .limit(1)
            .execute()
        )

        if result.data:

            return bool(
                result.data[0].get(
                    "enabled",
                    True
                )
            )

    except Exception as e:

        print(
            "Moderation settings error:",
            e,
            flush=True
        )

    return MODERATION_ENABLED_DEFAULT


def set_moderation_enabled(
    chat_id,
    enabled
):

    try:

        existing = (
            supabase
            .table("bot_moderation_settings")
            .select("id")
            .eq(
                "chat_id",
                db_chat_id(chat_id)
            )
            .limit(1)
            .execute()
        )

        data = {
            "chat_id":
                db_chat_id(chat_id),

            "enabled":
                bool(enabled),

            "updated_at":
                utc_now()
        }

        if existing.data:

            (
                supabase
                .table("bot_moderation_settings")
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
                .table("bot_moderation_settings")
                .insert(data)
                .execute()
            )

        set_bot_setting("moderation_enabled", bool(enabled), ADMIN_ID)
        return True

    except Exception as e:

        notify_admin_error(
            "Moderation settings",
            e,
            f"chat={chat_id}"
        )

        return False


# =========================================================
# MUTE / BAN STATE
# =========================================================

def parse_iso_timestamp(value):

    if not value:
        return None

    try:

        return datetime.fromisoformat(
            value.replace(
                "Z",
                "+00:00"
            )
        ).timestamp()

    except Exception:

        return None


def is_user_muted(
    chat_id,
    user_id
):

    state = get_moderation_state(
        chat_id,
        user_id
    )

    muted_until = parse_iso_timestamp(
        state.get("muted_until")
    )

    if not muted_until:
        return False

    if time.time() < muted_until:
        return True

    update_moderation_state(
        chat_id,
        user_id,
        muted_until=None
    )

    return False


def is_user_banned(
    chat_id,
    user_id
):

    state = get_moderation_state(
        chat_id,
        user_id
    )

    return bool(
        state.get(
            "banned",
            False
        )
    )


def set_user_mute(
    chat_id,
    user_id,
    minutes
):

    expires = (
        time.time()
        +
        max(
            1,
            int(minutes)
        ) * 60
    )

    expires_iso = datetime.fromtimestamp(
        expires,
        timezone.utc
    ).isoformat()

    return update_moderation_state(
        chat_id,
        user_id,
        muted_until=expires_iso
    )


def clear_user_mute(
    chat_id,
    user_id
):

    return update_moderation_state(
        chat_id,
        user_id,
        muted_until=None
    )


def set_user_ban(
    chat_id,
    user_id,
    value
):

    return update_moderation_state(
        chat_id,
        user_id,
        banned=bool(value)
    )


# =========================================================
# VK DELETE MESSAGE
# =========================================================

def delete_vk_message(
    peer_id,
    message
):

    cmid = (
        message.get(
            "conversation_message_id"
        )
        or message.get(
            "cmid"
        )
    )

    message_id = message.get(
        "id"
    )

    try:

        params = {
            "access_token":
                VK_TOKEN,

            "v":
                VK_VERSION,

            "delete_for_all":
                1
        }

        if cmid is not None:

            params["peer_id"] = int(
                peer_id
            )

            params["cmids"] = int(
                cmid
            )

        elif message_id is not None:

            params["message_ids"] = int(
                message_id
            )

        else:

            return False

        response = requests.post(
            f"{VK_API}/messages.delete",
            data=params,
            timeout=15
        )

        result = response.json()

        if "error" in result:

            print(
                "VK DELETE ERROR:",
                result["error"],
                flush=True
            )

            return False

        return True

    except Exception as e:

        print(
            "VK delete exception:",
            e,
            flush=True
        )

        return False


# =========================================================
# VK REMOVE USER FROM CHAT
# =========================================================

def remove_vk_chat_user(
    peer_id,
    user_id
):

    try:

        response = requests.post(
            f"{VK_API}/messages.removeChatUser",
            data={
                "access_token":
                    VK_TOKEN,

                "v":
                    VK_VERSION,

                "chat_id":
                    int(peer_id) - 2000000000,

                "user_id":
                    int(user_id)
            },
            timeout=15
        )

        result = response.json()

        if "error" in result:

            print(
                "VK REMOVE USER ERROR:",
                result["error"],
                flush=True
            )

            return False

        return True

    except Exception as e:

        print(
            "VK remove user exception:",
            e,
            flush=True
        )

        return False


# =========================================================
# ADMIN NOTIFY
# =========================================================

def send_vk_private_message(
    user_id,
    text
):

    try:
        uid = int(user_id)
    except Exception:
        return None

    if uid not in PROTECTED_IDS:
        return None

    if not text:
        return None

    try:

        response = requests.post(
            f"{VK_API}/messages.send",
            data={
                "access_token":
                    VK_TOKEN,

                "v":
                    VK_VERSION,

                "peer_id":
                    int(user_id),

                "message":
                    text[:4096],

                "random_id":
                    random.randint(
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


def _send_role_notification(user_ids, text, cooldown_key, cooldown):
    if not user_ids or not text:
        return
    now = time.time()
    with role_notify_lock:
        previous = role_notify_until.get(cooldown_key, 0)
        if now < previous:
            return
        role_notify_until[cooldown_key] = now + cooldown
    for uid in user_ids:
        send_vk_private_message(uid, text)


def notify_admin_moderation(chat_id, user_id, user_name, action, reason, warnings, message_text="", severity="low", confidence=None, rule_id="", uncertain=False):
    if not get_bot_setting("admin_notifications"):
        return
    confidence_text = "—" if confidence is None else f"{float(confidence):.2f}"
    message = (
        "[V1.3.5] [MODERATION]\n\n"
        f"👤 {user_name or 'Пользователь'}\n"
        f"🆔 VK ID: {user_id}\n"
        f"💬 Чат: {chat_id}\n"
        f"📌 Правило: {rule_id or 'не определено'}\n"
        f"⚠️ Категория: {severity}\n"
        f"🎯 Уверенность: {confidence_text}\n"
        f"🔧 Действие: {action}\n"
        f"🔢 Нарушений: {warnings}\n"
        f"❓ Неопределённость: {'ДА' if uncertain else 'НЕТ'}\n"
        f"📝 Причина: {reason[:1000]}\n"
        f"💬 Сообщение: {message_text[:2500]}"
    )
    key = f"admin:{chat_id}:{user_id}:{action}:{reason[:120]}"
    _send_role_notification({ADMIN_ID}, message, key, int(get_bot_setting("admin_alert_cooldown") or ADMIN_ALERT_COOLDOWN))
    if uncertain and get_bot_setting("moderator_notifications"):
        _send_role_notification(SENIOR_MODERATOR_IDS, message, "senior:" + key, int(get_bot_setting("moderator_alert_cooldown") or MODERATOR_ALERT_COOLDOWN))


def notify_tester_result(chat_id, user_id, user_name, result, action="none"):
    if not get_bot_setting("tester_notifications"):
        return
    text = (
        "[V1.3.5] [TEST] Модерация\n\n"
        f"👤 {user_name or 'Тестер'} (VK ID {user_id})\n"
        f"💬 Чат: {chat_id}\n"
        f"🧪 Результат: {json.dumps(result, ensure_ascii=False)}\n"
        f"🔧 Предложенное действие: {action}\n"
        "Реальное наказание: НЕ ПРИМЕНЕНО"
    )
    _send_role_notification({user_id}, text, f"tester:{chat_id}:{user_id}", 5)


# =========================================================
# MODERATION FAST FILTER
# =========================================================

MODERATION_TRIGGER_WORDS = (
    "идиот",
    "дебил",
    "тупой",
    "тупая",
    "тупое",
    "мудак",
    "долбо",
    "придур",
    "кретин",
    "лох",
    "чмо",
    "чмош",
    "тварь",
    "ублюд",
    "дегенерат",
    "даун",
    "пошел нах",
    "пошёл нах",
    "заткнись",
    "убью",
    "убить",
    "убей",
    "сдохни",
    "дохни",
    "угрож",
    "мошенн",
    "скам",
    "фишинг",
    "пароль",
    "карта",
    "cvv"
)


def moderation_candidate(text):

    low = normalize_text(
        text
    ).lower()

    if not low:
        return False

    if len(low) < MODERATION_MIN_TEXT_LENGTH:
        return False

    return any(
        word in low
        for word in MODERATION_TRIGGER_WORDS
    )


# =========================================================
# MODERATION AI
# =========================================================

def parse_moderation_result(text):

    cleaned = clean_model_text(
        text
    )

    if not cleaned:
        return None

    # Ищем JSON даже если модель добавила текст.
    match = re.search(
        r"\{.*\}",
        cleaned,
        re.DOTALL
    )

    if match:

        raw = match.group(0)

        try:
            import json

            data = json.loads(
                raw
            )

            return data

        except Exception:
            pass

    upper = cleaned.upper()

    if "IGNORE" in upper:
        return {
            "violation": False
        }

    return None


def ask_moderation_model(
    text,
    context
):

    prompt = f"""
Ты — строгий классификатор модерации чата.

Сообщение:
{text}

Контекст:
{context}

Твоя задача — определить, является ли сообщение
реальным нарушением правил.

ВАЖНО:

НЕ считать нарушением:
- обычный мат;
- игровой спор;
- критику танка;
- критику игры;
- игровые подколы;
- дружеские шутки;
- обычное несогласие;
- эмоциональную реакцию;
- "слабо сыграл";
- "ты рачина" в обычном игровом контексте,
  если это просто игровой подкол;
- спор о ТТХ.

Считать нарушением:
- серьёзное личное оскорбление;
- травлю;
- целенаправленное унижение;
- угрозы;
- угрозы жизни/насилия;
- публикацию чужих личных данных;
- попытку обмана/фишинг/скам;
- массовый рекламный спам;
- повторяющийся флуд;
- призывы к травле;
- серьёзные атаки на семью или личную жизнь.

Если сомневаешься —
violation=false.

Уровни:

low:
обычное нарушение без серьёзной угрозы.

medium:
прямое личное оскорбление/травля.

high:
угроза, опасное преследование, доксинг,
фишинг, серьёзная травля.

critical:
явная угроза физического насилия,
доксинг или особо опасное нарушение.

Верни ТОЛЬКО JSON:

{{
  "violation": true,
  "severity": "low",
  "rule_id": "1.1",
  "confidence": 0.95,
  "reason": "короткая причина"
}}

или:

{{
  "violation": false
}}

Никакого другого текста.
"""

    messages = [
        {
            "role":
                "system",

            "content":
                (
                    "Ты осторожный модератор. "
                    "Не модерируй обычный игровой срач."
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

        result = ask_model(
            MODERATION_MODEL,
            messages,
            MODERATION_MAX_TOKENS
        )

        return parse_moderation_result(
            result
        )

    except Exception as e:

        print(
            "Moderation AI error:",
            e,
            flush=True
        )

        return None


# =========================================================
# APPLY AUTOMATIC MODERATION
# =========================================================

def log_moderation_event(chat_id, user_id, user_name, rule_id, severity, confidence, message_text, action, warning_number, mute_until=None, review_status="auto"):
    try:
        supabase.table("bot_moderation").insert({
            "chat_id": db_chat_id(chat_id),
            "user_id": db_user_id(user_id),
            "user_name": user_name or "",
            "rule_id": rule_id or "",
            "severity": severity,
            "confidence": float(confidence or 0),
            "message": str(message_text)[:4000],
            "action": action,
            "warning_number": int(warning_number or 0),
            "mute_until": mute_until,
            "created_at": utc_now(),
            "review_status": review_status,
            "reviewed_by": None
        }).execute()
    except Exception as e:
        print("[V1.3.5] [MODERATION] bot_moderation log error:", e, flush=True)


def apply_automatic_moderation(chat_id, sender_id, user_name, text, message):
    if is_admin(sender_id):
        return False
    # Старшие модераторы защищены от автомата. Тестер проходит отдельную
    # тестовую ветку, где реальные наказания запрещены.
    if is_senior_moderator(sender_id):
        return False
    if is_tester(sender_id) and not get_bot_setting("moderation_test_mode"):
        return False
    if not get_moderation_enabled(chat_id):
        return False
    if is_user_banned(chat_id, sender_id):
        delete_vk_message(chat_id, message)
        return True
    if is_user_muted(chat_id, sender_id):
        delete_vk_message(chat_id, message)
        return True
    if not moderation_candidate(text):
        return False

    history = get_chat_memory(chat_id, 8)
    context = "\n".join(f"{item.get('speaker_name') or 'Участник'}: {item.get('content') or ''}" for item in history if item.get('content'))
    result = ask_moderation_model(text, context)
    if not result or not bool(result.get("violation", False)):
        return False

    severity = str(result.get("severity", "low")).lower()
    if severity not in ("low", "medium", "high", "critical"):
        severity = "low"
    reason = normalize_text(result.get("reason", "Нарушение правил")) or "Нарушение правил"
    rule_id = normalize_text(result.get("rule_id", ""))
    try:
        confidence = float(result.get("confidence", 0))
    except Exception:
        confidence = 0.0

    uncertain = confidence < float(get_bot_setting("confidence_threshold") or MODERATION_CONFIDENCE_THRESHOLD)
    current = get_warning_count(chat_id, sender_id)
    if uncertain:
        notify_admin_moderation(chat_id, sender_id, user_name, "проверка модератором", reason, current, text, severity, confidence, rule_id, True)
        log_moderation_event(chat_id, sender_id, user_name, rule_id, severity, confidence, text, "review", current, review_status="pending")
        if is_tester(sender_id) and get_bot_setting("moderation_test_mode"):
            notify_tester_result(chat_id, sender_id, user_name, result, "review")
        return False

    if is_tester(sender_id) and get_bot_setting("moderation_test_mode"):
        next_warning = current + 1
        if next_warning >= 4:
            proposed = f"mute {int(get_bot_setting('mute_4_duration'))} min"
        elif next_warning == 3:
            proposed = f"mute {int(get_bot_setting('mute_3_duration'))} min"
        elif next_warning == 2:
            proposed = f"mute {int(get_bot_setting('mute_2_duration'))} min"
        else:
            proposed = "warning"
        notify_admin_moderation(chat_id, sender_id, user_name, "ТЕСТ: " + proposed, reason, current, text, severity, confidence, rule_id, False)
        notify_tester_result(chat_id, sender_id, user_name, result, proposed)
        log_moderation_event(chat_id, sender_id, user_name, rule_id, severity, confidence, text, proposed, current, review_status="test")
        return False

    new_count = current + 1
    delete_vk_message(chat_id, message)
    if get_bot_setting("warning_enabled"):
        add_warning(chat_id, sender_id, user_name, reason, text, severity)

    mute_minutes = None
    if get_bot_setting("mute_enabled"):
        if new_count >= 4:
            mute_minutes = int(get_bot_setting("mute_4_duration"))
        elif new_count == 3:
            mute_minutes = int(get_bot_setting("mute_3_duration"))
        elif new_count == 2:
            mute_minutes = int(get_bot_setting("mute_2_duration"))

    action = "warning"
    mute_until = None
    if mute_minutes:
        set_user_mute(chat_id, sender_id, mute_minutes)
        mute_until = get_moderation_state(chat_id, sender_id).get("muted_until")
        action = f"mute {mute_minutes} min"

    if new_count >= 5 or severity in ("high", "critical"):
        notify_admin_moderation(chat_id, sender_id, user_name, action if new_count < 5 else "manual review", reason, new_count, text, severity, confidence, rule_id, False)

    if mute_minutes:
        send_message(chat_id, f"🔇 {user_name or 'Участник'}, сообщение удалено. Выдан мут на {mute_minutes} мин.\nПричина: {reason}")
    elif get_bot_setting("warning_enabled"):
        send_message(chat_id, f"⚠️ {user_name or 'Участник'}, предупреждение.\nПричина: {reason}\nНарушений: {new_count}")

    log_moderation_event(chat_id, sender_id, user_name, rule_id, severity, confidence, text, action, new_count, mute_until=mute_until, review_status="auto")
    return True


# =========================================================
# ADMIN TARGET PARSER
# =========================================================

def extract_target_user_id(
    message,
    text
):

    # 1. Ответ на сообщение пользователя
    reply = message.get(
        "reply_message"
    )

    if reply:

        reply_from = reply.get(
            "from_id"
        )

        if reply_from is not None:

            try:
                return int(
                    reply_from
                )
            except Exception:
                pass

    # 2. [id123|Имя]
    match = re.search(
        r"\[id(\d+)\|",
        text or "",
        re.IGNORECASE
    )

    if match:

        try:
            return int(
                match.group(1)
            )
        except Exception:
            pass

    # 3. @id123
    match = re.search(
        r"@id(\d+)\b",
        text or "",
        re.IGNORECASE
    )

    if match:

        try:
            return int(
                match.group(1)
            )
        except Exception:
            pass

    # 4. просто VK ID
    match = re.search(
        r"\b(?:id\s*)?(\d{5,12})\b",
        text or "",
        re.IGNORECASE
    )

    if match:

        try:
            return int(
                match.group(1)
            )
        except Exception:
            pass

    return None


# =========================================================
# ADMIN MODERATION COMMANDS
# =========================================================

def moderation_user_info(
    chat_id,
    user_id
):

    name = get_vk_user_name(
        user_id
    )

    state = get_moderation_state(
        chat_id,
        user_id
    )

    warnings = int(
        state.get(
            "warnings",
            0
        )
    )

    muted_until = state.get(
        "muted_until"
    )

    banned = bool(
        state.get(
            "banned",
            False
        )
    )

    logs = get_user_moderation_logs(
        chat_id,
        user_id,
        8
    )

    lines = [
        "🛡 МОДЕРАЦИЯ ПОЛЬЗОВАТЕЛЯ",
        "",
        f"👤 {name or 'Пользователь'}",
        f"🆔 ID: {user_id}",
        f"⚠️ Нарушений: {warnings}",
        f"🚫 Бан: {'ДА' if banned else 'НЕТ'}"
    ]

    if muted_until:

        timestamp = parse_iso_timestamp(
            muted_until
        )

        if timestamp and timestamp > time.time():

            remaining = int(
                (timestamp - time.time()) / 60
            )

            lines.append(
                f"🔇 Мут: ещё примерно "
                f"{max(1, remaining)} мин."
            )

        else:

            lines.append(
                "🔊 Мут: нет"
            )

    else:

        lines.append(
            "🔊 Мут: нет"
        )

    if logs:

        lines.append("")
        lines.append("📋 Последние нарушения:")

        for index, item in enumerate(
            logs,
            1
        ):

            action = item.get(
                "action",
                "?"
            )

            reason = normalize_text(
                item.get(
                    "reason",
                    ""
                )
            )

            if len(reason) > 120:
                reason = reason[:120] + "..."

            lines.append(
                f"{index}. {action}: {reason}"
            )

    return "\n".join(
        lines
    )


def handle_moderation_admin_command(
    chat_id,
    sender_id,
    text,
    message
):

    raw = normalize_text(text)
    low = raw.lower()

    if low in ("бот модерация тест", "бот, модерация тест", "бот модерация тест выкл", "бот, модерация тест выкл"):
        if not (is_admin(sender_id) or is_tester(sender_id)):
            return False, None
        enabled = not low.endswith("выкл")
        set_bot_setting("moderation_test_mode", enabled, sender_id)
        return True, "🧪 Тестовый режим модерации " + ("включён. Реальные наказания не применяются." if enabled else "выключен.")

    if not is_admin(sender_id):
        return False, None

    # -----------------------------------------------------
    # MODERATION ON
    # -----------------------------------------------------

    if low in (
        "бот модерация включи",
        "бот, модерация включи",
        "бот включи модерацию",
        "бот, включи модерацию",
        "бот модерацию включи",
        "бот, модерацию включи"
    ):

        if set_moderation_enabled(
            chat_id,
            True
        ):

            return True, (
                "🛡 Модерация включена."
            )

        return True, (
            "❌ Не удалось включить модерацию."
        )

    # -----------------------------------------------------
    # MODERATION OFF
    # -----------------------------------------------------

    if low in (
        "бот модерация выключи",
        "бот, модерация выключи",
        "бот выключи модерацию",
        "бот, выключи модерацию",
        "бот модерацию выключи",
        "бот, модерацию выключи"
    ):

        if set_moderation_enabled(
            chat_id,
            False
        ):

            return True, (
                "🛑 Модерация выключена.\n"
                "Память и самообучение продолжают работать."
            )

        return True, (
            "❌ Не удалось выключить модерацию."
        )

    # -----------------------------------------------------
    # STATUS
    # -----------------------------------------------------

    if low in (
        "бот статус модерации",
        "бот, статус модерации",
        "бот модерация статус",
        "бот, модерация статус"
    ):

        enabled = get_moderation_enabled(
            chat_id
        )

        return True, (
            "🛡 СТАТУС МОДЕРАЦИИ\n\n"
            f"Состояние: "
            f"{'🟢 ВКЛ' if enabled else '🛑 ВЫКЛ'}\n"
            f"Модель: {MODERATION_MODEL}\n"
            f"Мут: автоматический\n"
            f"Удаление сообщений: автоматическое\n"
            f"Предупреждения: включены\n"
            f"Бан: только вручную администратором"
        )

    # -----------------------------------------------------
    # USER INFO
    # -----------------------------------------------------

    if low.startswith(
        "бот нарушения"
    ):

        target_id = extract_target_user_id(
            message,
            raw
        )

        if not target_id:

            return True, (
                "❌ Укажи пользователя.\n\n"
                "Можно ответить на его сообщение:\n"
                "бот нарушения\n\n"
                "Или:\n"
                "бот нарушения 123456"
            )

        return True, moderation_user_info(
            chat_id,
            target_id
        )

    # -----------------------------------------------------
    # CLEAR WARNINGS
    # -----------------------------------------------------

    if low.startswith(
        "бот сбросить нарушения"
    ):

        target_id = extract_target_user_id(
            message,
            raw
        )

        if not target_id:

            return True, (
                "❌ Ответь на сообщение пользователя "
                "или укажи его ID."
            )

        if clear_user_warnings(
            chat_id,
            target_id
        ):

            return True, (
                f"🧹 Нарушения пользователя "
                f"{target_id} сброшены."
            )

        return True, (
            "❌ Не удалось сбросить нарушения."
        )

    # -----------------------------------------------------
    # MUTE
    # -----------------------------------------------------

    mute_match = re.match(
        r"^бот\s+мут\s+(\d+)",
        low
    )

    if mute_match:

        minutes = int(
            mute_match.group(1)
        )

        target_id = extract_target_user_id(
            message,
            raw
        )

        if not target_id:

            return True, (
                "❌ Ответь на сообщение пользователя "
                "и напиши:\n"
                "бот мут 10"
            )

        if set_user_mute(
            chat_id,
            target_id,
            minutes
        ):

            return True, (
                f"🔇 Пользователь {target_id} "
                f"замьючен на {minutes} мин."
            )

        return True, (
            "❌ Не удалось выдать мут."
        )

    # -----------------------------------------------------
    # UNMUTE
    # -----------------------------------------------------

    if low in (
        "бот размут",
        "бот, размут",
        "бот снять мут",
        "бот, снять мут"
    ):

        target_id = extract_target_user_id(
            message,
            raw
        )

        if not target_id:

            return True, (
                "❌ Ответь на сообщение пользователя "
                "и напиши:\n"
                "бот размут"
            )

        if clear_user_mute(
            chat_id,
            target_id
        ):

            return True, (
                f"🔊 Мут с пользователя "
                f"{target_id} снят."
            )

        return True, (
            "❌ Не удалось снять мут."
        )

    # -----------------------------------------------------
    # BAN
    # -----------------------------------------------------

    if low in (
        "бот бан",
        "бот, бан",
        "бот забанить",
        "бот, забанить"
    ):

        target_id = extract_target_user_id(
            message,
            raw
        )

        if not target_id:

            return True, (
                "❌ Ответь на сообщение пользователя "
                "и напиши:\n"
                "бот бан"
            )

        set_user_ban(
            chat_id,
            target_id,
            True
        )

        remove_vk_chat_user(
            chat_id,
            target_id
        )

        return True, (
            f"🚫 Пользователь {target_id} "
            f"заблокирован и удалён из чата."
        )

    # -----------------------------------------------------
    # UNBAN
    # -----------------------------------------------------

    if low in (
        "бот разбан",
        "бот, разбан",
        "бот снять бан",
        "бот, снять бан"
    ):

        target_id = extract_target_user_id(
            message,
            raw
        )

        if not target_id:

            return True, (
                "❌ Ответь на сообщение пользователя "
                "и напиши:\n"
                "бот разбан"
            )

        if set_user_ban(
            chat_id,
            target_id,
            False
        ):

            return True, (
                f"🔓 Бан пользователя "
                f"{target_id} снят."
            )

        return True, (
            "❌ Не удалось снять бан."
        )

    if low in ("бот модераторы", "бот, модераторы"):
        senior = ", ".join(str(x) for x in sorted(SENIOR_MODERATOR_IDS)) or "не назначены"
        junior = ", ".join(str(x) for x in sorted(JUNIOR_MODERATOR_IDS)) or "нет"
        return True, f"🛡 Старшие модераторы: {senior}\n🧪 Младшие/тестеры: {junior}\n👑 Админ: {ADMIN_ID}"

    if low in ("бот тестер", "бот, тестер"):
        return True, f"🧪 Тестер: {', '.join(str(x) for x in sorted(TESTER_IDS)) or 'не назначен'}"

    return False, None


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

    if low in (
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
    ):

        if set_learning_enabled(
            chat_id,
            False
        ):

            return True, (
                "🛑 Автообучение выключено.\n\n"
                "Старая память НЕ удалена.\n"
                "Официальная память работает.\n"
                "Ручное «Запомни...» работает."
            )

        return True, (
            "❌ Не удалось выключить обучение."
        )

    if low in (
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

    messages = get_chat_message_count(
        chat_id
    )

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
        f"🛡 Модерация: "
        f"{'🟢 включена' if get_moderation_enabled(chat_id) else '🛑 выключена'}\n"
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
        "👑 АДМИН-ПАНЕЛЬ V1.3.5\n\n"

        "🛡 МОДЕРАЦИЯ\n"
        "бот модерация включи\n"
        "бот модерация выключи\n"
        "бот статус модерации\n"
        "бот нарушения — ответом на сообщение\n"
        "бот сбросить нарушения — ответом\n"
        "бот мут 10 — ответом\n"
        "бот размут — ответом\n"
        "бот бан — ответом\n"
        "бот разбан — только вручную, ответом\n"
        "бот модераторы\n"
        "бот тестер\n"
        "бот модерация тест\n"
        "бот модерация тест выкл\n\n"

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
        "бот разработчик статус\n"
        "бот разработчик обнови\n"
        "бот разработчик покажи изменения\n"
        "бот разработчик примени\n"
        "бот разработчик отмена\n"
        "бот разработчик откати\n"
        "бот разработчик статус\n"
        "бот разработчик обнови\n"
        "бот разработчик покажи изменения\n"
        "бот разработчик примени\n"
        "бот разработчик отмена\n"
        "бот разработчик откати\n"
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
                "❌ Укажи ID знания."
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
            "⚠️ Для очистки всех знаний:\n"
            "бот знания очистить ПОДТВЕРЖДАЮ"
        )

    if low in (
        "бот знания очистить подтверждаю",
        "бот, знания очистить подтверждаю"
    ):

        if clear_knowledge(chat_id):

            return True, (
                "🧹 Автоматические знания удалены.\n"
                "Официальная память не затронута."
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

            return True, (
                "🔄 Счётчик самообучения сброшен."
            )

        except Exception as e:

            notify_admin_error(
                "Learning reset",
                e,
                f"chat={chat_id}"
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
            "🔇 Обычные ответы бота выключены.\n"
            "Память, обучение и модерация продолжают работать."
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
            "🔊 Обычные ответы бота включены."
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
            f"Модерация: "
            f"{'🟢 включена' if get_moderation_enabled(chat_id) else '🛑 выключена'}\n"
            f"Защита участников: 🛡 включена\n"
            f"Официальная память: 📚 включена\n"
            f"Тестеров: {len(TESTER_IDS)}\n"
            f"Основная модель: {MAIN_MODEL}\n"
            f"Резервная модель: {BACKUP_MODEL}\n"
            f"Модель модерации: {MODERATION_MODEL}\n"
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

        history = filter_learning_history(
            history
        )

        if len(history) < 5:

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

            except Exception:
                pass

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

Сохраняй ТОЛЬКО информацию,
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
- игровые факты конкретного чата.

Не сохраняй:
- работу;
- школу;
- здоровье;
- отношения;
- личную жизнь;
- адреса;
- документы;
- пароли;
- банковские данные;
- политику;
- религию;
- срачи;
- оскорбления;
- случайный флуд;
- слухи;
- эмоции;
- предположения.

Мнение не превращай в факт.

Формат:

USER|ID|Факт

или

CHAT|Факт|важность

Если полезных данных нет:

NONE

Данные:

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
                            "игровых знаний."
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
            f"chat={chat_id} | stage={stage}",
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

                if int(from_id) < 0:
                    return True

            except Exception:
                pass

    if re.search(
        r"\[club\d+\|",
        low
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


def local_offtopic_reply(text):
    low = normalize_text(text).lower()
    if low in ("бот как дела", "бот как ты", "бот, как дела", "эй бот как дела"):
        return "Нормально 😄 Я тут в основном по Tanks Blitz."
    return "Я в основном по Tanks Blitz. По игре спрашивай — помогу."


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

        return is_directed_to_bot_telegram(
            message,
            text
        )

    return is_directed_to_bot_vk(
        message,
        text
    )


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
- шутки;
- несогласие;
- игровые подколы.

Если вмешиваться не надо:

NONE

Если надо:
одна короткая реплика.

Максимум 2 предложения.

Если сомневаешься — NONE.
"""

    messages = [
        {
            "role":
                "system",

            "content":
                "Ты осторожный классификатор личных конфликтов."
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
            +
            INTERVENTION_COOLDOWN
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
            "Все текстовые AI временно недоступны."
        )


# =========================================================
# ADMIN ERROR
# =========================================================

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
# GITHUB / AI DEVELOPER ENGINE
# =========================================================

GITHUB_FILE_PATH = os.environ.get(
    "GITHUB_FILE_PATH",
    "vk_bot.py"
).strip()

DEVELOPER_TIMEOUT = 60

developer_engine_lock = threading.Lock()


def github_headers():
    if not DEVELOPER_TOKEN:
        raise RuntimeError("GITHUB_TOKEN не установлен")

    return {
        "Authorization": f"Bearer {DEVELOPER_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28"
    }


def github_file_url():
    if not DEVELOPER_REPO:
        raise RuntimeError("GITHUB_REPO не установлен")

    return (
        f"https://api.github.com/repos/"
        f"{DEVELOPER_REPO}/contents/"
        f"{GITHUB_FILE_PATH}"
    )


def github_get_file():
    response = requests.get(
        github_file_url(),
        headers=github_headers(),
        params={
            "ref": DEVELOPER_BRANCH
        },
        timeout=DEVELOPER_TIMEOUT
    )

    if response.status_code != 200:
        raise RuntimeError(
            f"GitHub GET {response.status_code}: "
            f"{response.text[:1000]}"
        )

    data = response.json()

    if data.get("encoding") != "base64":
        raise RuntimeError(
            "GitHub вернул файл не в base64"
        )

    import base64

    content = base64.b64decode(
        data["content"]
    ).decode("utf-8")

    return content, data["sha"]


def github_test_connection():
    content, sha = github_get_file()

    return {
        "ok": True,
        "file": GITHUB_FILE_PATH,
        "branch": DEVELOPER_BRANCH,
        "size": len(content),
        "sha": sha
    }

# =========================================================
# DEVELOPER MODE
# =========================================================

DEVELOPER_MAX_AI_TOKENS = 12000
DEVELOPER_FILE_PATH = os.environ.get(
    "GITHUB_FILE_PATH",
    "vk_bot (2).py"
).strip()

developer_patch_lock = threading.Lock()
developer_pending_patch = {}


def developer_status():
    with developer_patch_lock:
        instruction = developer_pending_patch.get("instruction")
        has_patch = bool(developer_pending_patch.get("new_source"))
        busy = bool(developer_pending_patch.get("busy"))
        backup = developer_pending_patch.get("backup")

    return (
        "[V1.3.5] [DEVELOPER]\n\n"
        f"Режим: "
        f"{'🟢 включён' if DEVELOPER_ENABLED else '🔴 выключен'}\n"
        f"Репозиторий: "
        f"{DEVELOPER_REPO or 'не задан'}\n"
        f"Файл: "
        f"{DEVELOPER_FILE_PATH}\n"
        f"Ветка: "
        f"{DEVELOPER_BRANCH}\n"
        f"GitHub token: "
        f"{'🟢 задан' if DEVELOPER_TOKEN else '🔴 не задан'}\n"
        f"AI-разработка: "
        f"{'🟢 выполняется' if busy else '⚪ свободен'}\n"
        f"Изменения: "
        f"{'🟢 подготовлены' if has_patch else '⚪ нет'}\n"
        f"Последний backup: "
        f"{backup or 'нет'}\n"
        f"Инструкция: "
        f"{'📝 есть' if instruction else '⚪ нет'}\n\n"
        "Автоприменение без подтверждения: 🔴 запрещено"
    )


def developer_parse_repo():
    repo = (DEVELOPER_REPO or "").strip()

    if not repo:
        raise RuntimeError(
            "GITHUB_REPO не задан."
        )

    repo = repo.replace(
        "https://github.com/",
        ""
    ).replace(
        "http://github.com/",
        ""
    ).strip("/")

    parts = repo.split("/")

    if len(parts) != 2:
        raise RuntimeError(
            "GITHUB_REPO должен иметь формат owner/repository."
        )

    return parts[0], parts[1]


def developer_github_headers():
    if not DEVELOPER_TOKEN:
        raise RuntimeError(
            "GITHUB_TOKEN не задан."
        )

    return {
        "Authorization":
            f"Bearer {DEVELOPER_TOKEN}",
        "Accept":
            "application/vnd.github+json",
        "X-GitHub-Api-Version":
            "2022-11-28",
        "User-Agent":
            "Tanks-Blitz-AI-Developer"
    }


def developer_github_get_file():
    owner, repo = developer_parse_repo()

    url = (
        "https://api.github.com/repos/"
        f"{owner}/{repo}/contents/"
        f"{DEVELOPER_FILE_PATH}"
    )

    response = requests.get(
        url,
        headers=developer_github_headers(),
        params={
            "ref": DEVELOPER_BRANCH
        },
        timeout=30
    )

    if response.status_code != 200:
        raise RuntimeError(
            "GitHub GET file "
            f"{response.status_code}: "
            f"{response.text[:1500]}"
        )

    data = response.json()

    if data.get("type") != "file":
        raise RuntimeError(
            "GitHub API не вернул файл."
        )

    encoded = data.get("content", "")
    sha = data.get("sha")

    if not encoded or not sha:
        raise RuntimeError(
            "GitHub не вернул content/sha."
        )

    try:
        source = base64.b64decode(
            encoded.replace("\n", "")
        ).decode("utf-8")
    except Exception as e:
        raise RuntimeError(
            f"Не удалось декодировать GitHub файл: {e}"
        )

    return source, sha


def developer_build_ai_messages(
    source,
    instruction
):
    system_prompt = """
Ты — внутренний AI-разработчик проекта Tanks Blitz AI.

Тебе передан текущий Python-файл бота и инструкция администратора.

Твоя задача — изменить КОД ТОЛЬКО настолько,
насколько необходимо для выполнения инструкции.

ВАЖНЫЕ ПРАВИЛА:

1. Не переписывай весь файл.
2. Не удаляй существующие функции без необходимости.
3. Сохраняй существующую архитектуру.
4. Не меняй API-ключи, токены и секреты.
5. Не добавляй реальные секреты в код.
6. Не меняй ADMIN_ID без прямой инструкции администратора.
7. Не отключай безопасность разработчика.
8. Не добавляй вредоносный код.
9. Не используй subprocess, os.system или shell-команды
   для выполнения пользовательских инструкций.
10. Изменения должны оставаться валидным Python-кодом.

ОТВЕТ ДОЛЖЕН БЫТЬ ТОЛЬКО В ФОРМАТЕ:

<CHANGE>
<OLD>
точный существующий фрагмент
</OLD>
<NEW>
новый фрагмент
</NEW>
</CHANGE>

Если нужно несколько изменений, используй несколько блоков <CHANGE>.

КРИТИЧЕСКИ ВАЖНО:
<OLD> должен дословно существовать в исходном файле.
Не сокращай его через "...".
Не используй номера строк вместо текста.

Если инструкция не требует изменения кода,
верни:

<NO_CHANGE>
Причина
</NO_CHANGE>
"""

    user_prompt = (
        "ИНСТРУКЦИЯ АДМИНИСТРАТОРА:\n"
        f"{instruction}\n\n"
        "ТЕКУЩИЙ ИСХОДНИК:\n"
        "```python\n"
        f"{source}\n"
        "```\n"
    )

    return [
        {
            "role": "system",
            "content": system_prompt
        },
        {
            "role": "user",
            "content": user_prompt
        }
    ]


def developer_extract_changes(ai_text):
    text = clean_model_text(
        ai_text or ""
    )

    if "<NO_CHANGE>" in text:
        return []

    pattern = re.compile(
        r"<CHANGE>\s*"
        r"<OLD>\s*(.*?)\s*</OLD>\s*"
        r"<NEW>\s*(.*?)\s*</NEW>\s*"
        r"</CHANGE>",
        re.DOTALL |
        re.IGNORECASE
    )

    matches = pattern.findall(text)

    if not matches:
        raise RuntimeError(
            "AI не вернул изменения в требуемом формате."
        )

    changes = []

    for old, new in matches:
        old = old.strip("\n")
        new = new.strip("\n")

        if not old:
            raise RuntimeError(
                "AI вернул пустой OLD-блок."
            )

        changes.append(
            {
                "old": old,
                "new": new
            }
        )

    return changes


def developer_apply_changes(
    source,
    changes
):
    result = source

    for index, change in enumerate(
        changes,
        start=1
    ):
        old = change["old"]
        new = change["new"]

        count = result.count(old)

        if count != 1:
            raise RuntimeError(
                f"Изменение #{index}: "
                f"OLD-фрагмент найден {count} раз. "
                "Безопасное изменение отменено."
            )

        result = result.replace(
            old,
            new,
            1
        )

    if result == source:
        raise RuntimeError(
            "После применения patch исходник не изменился."
        )

    return result


def developer_check_python(
    source
):
    try:
        compile(
            source,
            DEVELOPER_FILE_PATH,
            "exec"
        )
    except SyntaxError as e:
        location = (
            f"строка {e.lineno}"
            if e.lineno
            else "неизвестная строка"
        )

        raise RuntimeError(
            f"SyntaxError: {location}: "
            f"{e.msg}"
        )

    except Exception as e:
        raise RuntimeError(
            f"Ошибка проверки Python: {e}"
        )


def developer_make_diff(
    old_source,
    new_source
):
    old_lines = old_source.splitlines()
    new_lines = new_source.splitlines()

    import difflib

    diff = difflib.unified_diff(
        old_lines,
        new_lines,
        fromfile=DEVELOPER_FILE_PATH,
        tofile=DEVELOPER_FILE_PATH,
        lineterm=""
    )

    return "\n".join(diff)


def developer_run_ai(
    source,
    instruction
):
    messages = developer_build_ai_messages(
        source,
        instruction
    )

    return ask_model(
        MAIN_MODEL,
        messages,
        DEVELOPER_MAX_AI_TOKENS
    )


def developer_prepare_patch(
    instruction,
    admin_id
):
    try:
        if not DEVELOPER_ENABLED:
            send_vk_private_message(
                admin_id,
                "[V1.3.5] [DEVELOPER]\n"
                "🔴 Режим разработчика выключен."
            )
            return

        if not DEVELOPER_REPO:
            send_vk_private_message(
                admin_id,
                "[V1.3.5] [DEVELOPER]\n\n"
                "❌ Не задан GITHUB_REPO.\n"
                "Нужно указать:\n"
                "owner/repository"
            )
            return

        if not DEVELOPER_TOKEN:
            send_vk_private_message(
                admin_id,
                "[V1.3.5] [DEVELOPER]\n\n"
                "❌ Не задан GITHUB_TOKEN."
            )
            return

        send_vk_private_message(
            admin_id,
            "[V1.3.5] [DEVELOPER]\n\n"
            "🧠 Начинаю разработку...\n"
            "1️⃣ Получаю текущий код GitHub\n"
            "2️⃣ Анализирую инструкцию\n"
            "3️⃣ Формирую patch\n"
            "4️⃣ Проверяю Python\n"
            "5️⃣ Готовлю изменения\n\n"
            "Пока GitHub не изменяется."
        )

        source, github_sha = (
            developer_github_get_file()
        )

        ai_result = developer_run_ai(
            source,
            instruction
        )

        changes = developer_extract_changes(
            ai_result
        )

        if not changes:
            with developer_patch_lock:
                developer_pending_patch.clear()
                developer_pending_patch.update({
                    "instruction":
                        instruction,
                    "busy":
                        False,
                    "github_sha":
                        github_sha,
                    "new_source":
                        None,
                    "patch":
                        "",
                    "backup":
                        None
                })

            send_vk_private_message(
                admin_id,
                "[V1.3.5] [DEVELOPER]\n\n"
                "ℹ️ AI решил, что для этой "
                "инструкции изменение кода "
                "не требуется."
            )
            return

        new_source = developer_apply_changes(
            source,
            changes
        )

        developer_check_python(
            new_source
        )

        patch = developer_make_diff(
            source,
            new_source
        )

        if not patch.strip():
            raise RuntimeError(
                "Patch оказался пустым."
            )

        with developer_patch_lock:
            developer_pending_patch.clear()
            developer_pending_patch.update({
                "instruction":
                    instruction,
                "busy":
                    False,
                "github_sha":
                    github_sha,
                "source":
                    source,
                "new_source":
                    new_source,
                "patch":
                    patch,
                "backup":
                    None,
                "created_at":
                    datetime.now(
                        timezone.utc
                    ).isoformat()
            })

        send_vk_private_message(
            admin_id,
            "[V1.3.5] [DEVELOPER]\n\n"
            "✅ Изменения подготовлены.\n\n"
            f"📝 Изменений: {len(changes)}\n"
            "🐍 Syntax check: ✅\n"
            "💾 GitHub пока НЕ изменён.\n\n"
            "Команды:\n"
            "• бот разработчик покажи изменения\n"
            "• бот разработчик примени\n"
            "• бот разработчик отмена"
        )

    except Exception as e:
        with developer_patch_lock:
            developer_pending_patch["busy"] = False

        print(
            "Developer prepare error:",
            e,
            flush=True
        )

        send_vk_private_message(
            admin_id,
            "[V1.3.5] [DEVELOPER]\n\n"
            "❌ Ошибка разработки:\n"
            f"{str(e)[:2500]}"
        )


def developer_github_backup(
    source,
    timestamp
):
    owner, repo = developer_parse_repo()

    backup_path = (
        "backups/"
        f"developer_{timestamp}_"
        f"{os.path.basename(DEVELOPER_FILE_PATH)}"
    )

    url = (
        "https://api.github.com/repos/"
        f"{owner}/{repo}/contents/"
        f"{backup_path}"
    )

    encoded = base64.b64encode(
        source.encode("utf-8")
    ).decode("ascii")

    payload = {
        "message":
            f"[DEVELOPER] backup {timestamp}",
        "content":
            encoded,
        "branch":
            DEVELOPER_BRANCH
    }

    response = requests.put(
        url,
        headers=developer_github_headers(),
        json=payload,
        timeout=30
    )

    if response.status_code not in (
        200,
        201
    ):
        raise RuntimeError(
            "Backup GitHub "
            f"{response.status_code}: "
            f"{response.text[:1500]}"
        )

    return backup_path


def developer_github_commit(
    new_source,
    github_sha,
    instruction
):
    owner, repo = developer_parse_repo()

    url = (
        "https://api.github.com/repos/"
        f"{owner}/{repo}/contents/"
        f"{DEVELOPER_FILE_PATH}"
    )

    encoded = base64.b64encode(
        new_source.encode("utf-8")
    ).decode("ascii")

    short_instruction = re.sub(
        r"\s+",
        " ",
        instruction
    ).strip()

    commit_message = (
        "[DEVELOPER] "
        f"{short_instruction[:100]}"
    )

    payload = {
        "message":
            commit_message,
        "content":
            encoded,
        "sha":
            github_sha,
        "branch":
            DEVELOPER_BRANCH
    }

    response = requests.put(
        url,
        headers=developer_github_headers(),
        json=payload,
        timeout=30
    )

    if response.status_code not in (
        200,
        201
    ):
        raise RuntimeError(
            "GitHub commit "
            f"{response.status_code}: "
            f"{response.text[:2000]}"
        )

    data = response.json()

    return {
        "commit":
            data.get(
                "commit",
                {}
            ).get(
                "sha"
            ),
        "url":
            data.get(
                "commit",
                {}
            ).get(
                "html_url"
            ),
        "file_url":
            data.get(
                "content",
                {}
            ).get(
                "html_url"
            )
    }


def developer_apply_pending(
    admin_id
):
    try:
        with developer_patch_lock:
            pending = dict(
                developer_pending_patch
            )

        new_source = pending.get(
            "new_source"
        )

        old_source = pending.get(
            "source"
        )

        instruction = pending.get(
            "instruction"
        )

        github_sha = pending.get(
            "github_sha"
        )

        if not new_source:
            return (
                False,
                "❌ Нет подготовленных изменений."
            )

        if not old_source:
            return (
                False,
                "❌ Не найден исходный код для backup."
            )

        if not github_sha:
            return (
                False,
                "❌ Не найден GitHub SHA."
            )

        developer_check_python(
            new_source
        )

        timestamp = datetime.now(
            timezone.utc
        ).strftime(
            "%Y%m%d_%H%M%S"
        )

        send_vk_private_message(
            admin_id,
            "[V1.3.5] [DEVELOPER]\n\n"
            "💾 Делаю backup текущего файла..."
        )

        backup_path = developer_github_backup(
            old_source,
            timestamp
        )

        send_vk_private_message(
            admin_id,
            "[V1.3.5] [DEVELOPER]\n\n"
            "🚀 Backup создан.\n"
            f"📦 {backup_path}\n\n"
            "Теперь выполняю commit..."
        )

        # Перед commit ещё раз получаем текущий файл.
        # Это защищает от перезаписи чужого изменения,
        # сделанного после подготовки patch.
        current_source, current_sha = (
            developer_github_get_file()
        )

        if current_sha != github_sha:
            return (
                False,
                "❌ GitHub-файл уже изменился после "
                "подготовки patch.\n\n"
                "Изменения НЕ применены.\n"
                "Сделай новую инструкцию."
            )

        result = developer_github_commit(
            new_source,
            current_sha,
            instruction or "update"
        )

        with developer_patch_lock:
            developer_pending_patch.clear()

        commit_sha = (
            result.get("commit")
            or "неизвестно"
        )

        return (
            True,
            "[V1.3.5] [DEVELOPER]\n\n"
            "✅ ИЗМЕНЕНИЯ ПРИМЕНЕНЫ\n\n"
            f"📦 Backup: {backup_path}\n"
            f"🔗 Commit: {commit_sha}\n"
            f"🌿 Ветка: {DEVELOPER_BRANCH}\n\n"
            "🚀 GitHub обновлён.\n"
            "Render может начать автоматический deploy."
        )

    except Exception as e:
        print(
            "Developer apply error:",
            e,
            flush=True
        )

        return (
            False,
            "[V1.3.5] [DEVELOPER]\n\n"
            "❌ Ошибка применения:\n"
            f"{str(e)[:2500]}"
        )


def developer_show_patch():
    with developer_patch_lock:
        patch = developer_pending_patch.get(
            "patch"
        )

        instruction = (
            developer_pending_patch.get(
                "instruction"
            )
        )

        busy = bool(
            developer_pending_patch.get(
                "busy"
            )
        )

    if busy:
        return (
            "[V1.3.5] [DEVELOPER]\n\n"
            "🧠 Изменения ещё готовятся."
        )

    if not patch:
        return (
            "[V1.3.5] [DEVELOPER]\n\n"
            "⚪ Подготовленных изменений нет."
        )

    return (
        "[V1.3.5] [DEVELOPER]\n\n"
        "📝 Инструкция:\n"
        f"{instruction or '—'}\n\n"
        "🔧 PATCH:\n"
        "```diff\n"
        f"{patch[:12000]}\n"
        "```"
    )


def handle_developer_command(text):
    if not DEVELOPER_ENABLED:
        return (
            True,
            "[V1.3.5] [DEVELOPER]\n"
            "🔴 Режим разработчика выключен."
        )

    raw = normalize_text(
        text or ""
    ).strip()

    low = raw.lower()

    # ---------------------------------------------------------
    # STATUS
    # ---------------------------------------------------------

    if low in (
        "бот разработчик статус",
        "бот, разработчик статус",
        "бот developer статус"
    ):
        return True, developer_status()
        
        if low == "бот разработчик тест":
            try:
                result = github_test_connection()

                return True, (
                    "[V1.3.5] [DEVELOPER]\n\n"
                    "🧪 GitHub-тест успешен.\n\n"
                    f"Репозиторий: {DEVELOPER_REPO}\n"
                    f"Ветка: {DEVELOPER_BRANCH}\n"
                    f"Файл: {result['file']}\n"
                    f"Размер: {result['size']} символов\n"
                    "GitHub: 🟢 доступ есть"
                )

            except Exception as e:
                return True, (
                    "[V1.3.5] [DEVELOPER]\n\n"
                    "❌ GitHub-тест не пройден.\n\n"
                    f"Ошибка: {str(e)[:1500]}"
                )
    # ---------------------------------------------------------
    # SHOW PATCH
    # ---------------------------------------------------------

    if low in (
        "бот разработчик покажи изменения",
        "бот разработчик покажи patch",
        "бот разработчик покажи патч",
        "бот, разработчик покажи изменения"
    ):
        return True, developer_show_patch()

    # ---------------------------------------------------------
    # CANCEL
    # ---------------------------------------------------------

    if low in (
        "бот разработчик отмена",
        "бот разработчик отменить",
        "бот разработчик очисти",
        "бот, разработчик отмена"
    ):
        with developer_patch_lock:
            developer_pending_patch.clear()

        return (
            True,
            "[V1.3.5] [DEVELOPER]\n\n"
            "🗑 Подготовленные изменения "
            "полностью отменены.\n"
            "GitHub не изменён."
        )

    # ---------------------------------------------------------
    # APPLY
    # ---------------------------------------------------------

    if low in (
        "бот разработчик примени",
        "бот разработчик применить",
        "бот, разработчик примени"
    ):
        with developer_patch_lock:
            if developer_pending_patch.get(
                "busy"
            ):
                return (
                    True,
                    "[V1.3.5] [DEVELOPER]\n\n"
                    "⏳ Разработка ещё выполняется."
                )

            if not developer_pending_patch.get(
                "new_source"
            ):
                return (
                    True,
                    "[V1.3.5] [DEVELOPER]\n\n"
                    "❌ Нет подготовленного patch."
                )

            developer_pending_patch[
                "busy"
            ] = True

        def apply_worker():
            try:
                ok, reply = developer_apply_pending(
                    ADMIN_ID
                )

                send_vk_private_message(
                    ADMIN_ID,
                    reply
                )

            finally:
                with developer_patch_lock:
                    developer_pending_patch[
                        "busy"
                    ] = False

        threading.Thread(
            target=apply_worker,
            daemon=True
        ).start()

        return (
            True,
            "[V1.3.5] [DEVELOPER]\n\n"
            "🚀 Применение запущено.\n"
            "Сначала backup → затем проверка SHA → "
            "commit в GitHub."
        )

    # ---------------------------------------------------------
    # ROLLBACK INFORMATION
    # ---------------------------------------------------------

    if low in (
        "бот разработчик откати",
        "бот разработчик rollback",
        "бот, разработчик откати"
    ):
        return (
            True,
            "[V1.3.5] [DEVELOPER]\n\n"
            "↩️ Автоматический rollback "
            "через эту команду пока не выполняется.\n\n"
            "Backup создаётся перед каждым commit."
        )

    # ---------------------------------------------------------
    # START DEVELOPMENT
    # ---------------------------------------------------------

    if raw and not low.startswith(
        "бот разработчик"
    ):
        with developer_patch_lock:
            if developer_pending_patch.get(
                "busy"
            ):
                return (
                    True,
                    "[V1.3.5] [DEVELOPER]\n\n"
                    "⏳ Предыдущая разработка ещё "
                    "выполняется.\n"
                    "Дождись её завершения."
                )

            developer_pending_patch.clear()

            developer_pending_patch.update({
                "instruction":
                    raw,
                "busy":
                    True,
                "new_source":
                    None,
                "source":
                    None,
                "patch":
                    "",
                "github_sha":
                    None,
                "backup":
                    None
            })

        threading.Thread(
            target=developer_prepare_patch,
            args=(
                raw,
                ADMIN_ID
            ),
            daemon=True
        ).start()

        return (
            True,
            "[V1.3.5] [DEVELOPER]\n\n"
            "🧠 Инструкция принята.\n"
            "Начинаю анализ текущего кода.\n\n"
            "GitHub пока НЕ изменяется."
        )

    return False, None


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
                "access_token":
                    VK_TOKEN,

                "v":
                    VK_VERSION,

                "peer_id":
                    int(peer_id),

                "message":
                    text[:4096],

                "random_id":
                    random.randint(
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

                if random.random() > 0.08:
                    continue

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
                    "Если нечего сказать — NONE."
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
# HOME
# =========================================================

@app.route(
    "/",
    methods=["GET"]
)
def home():

    learning_status = None
    moderation_status = None

    try:

        if ALLOWED_VK_PEER_ID:

            learning_status = is_learning_enabled(
                ALLOWED_VK_PEER_ID
            )

            moderation_status = get_moderation_enabled(
                ALLOWED_VK_PEER_ID
            )

    except Exception:

        learning_status = None
        moderation_status = None

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

        "moderation":
            True,

        "moderation_enabled":
            moderation_status,

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

        if int(peer_id) == int(sender_id) and is_admin(sender_id):
            dm_text = (message.get("text") or "").strip()
            handled, reply = handle_developer_command(dm_text) if dm_text else (False, None)
            if handled and reply:
                send_vk_private_message(sender_id, reply)
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
        # ADMIN TB COMMANDS
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

        # =====================================================
        # MODERATION ADMIN COMMANDS
        # =====================================================

        handled, reply = handle_moderation_admin_command(
            chat_id,
            sender_id,
            text,
            message
        )

        if handled:

            if reply:
                send_message(
                    peer_id,
                    reply
                )

            return "ok"

        # =====================================================
        # LEARNING COMMANDS
        # =====================================================

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

        # =====================================================
        # GENERAL ADMIN COMMANDS
        # =====================================================

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
        # MODERATION CHECK
        # =====================================================

        if apply_automatic_moderation(
            chat_id,
            sender_id,
            user_name,
            text,
            message
        ):

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

        if not is_game_relevant(text):
            send_message(peer_id, local_offtopic_reply(text))
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

        if not is_game_relevant(text):
            send_telegram_message(raw_chat_id, local_offtopic_reply(text), message.get("message_id"))
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
        f"🛡 MODERATION MODEL: {MODERATION_MODEL}",
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
        "🛡 Moderation: ENABLED",
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
