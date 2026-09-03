import os
import re
import time
import hashlib
import random
import threading
import base64
from datetime import datetime, timezone

import requests
from flask import Flask, request
from groq import Groq
from supabase import create_client


# =========================================================
# CONFIG
# =========================================================

BOT_VERSION = "V1.3"
BOT_BUILD = "Начальное самообучение + Telegram"

VK_TOKEN = os.environ.get("VK_TOKEN", "")
VK_CONFIRMATION_CODE = os.environ.get("VK_CONFIRMATION_CODE", "")
VK_GROUP_SECRET = os.environ.get("VK_GROUP_SECRET", "")

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
PERPLEXITY_API_KEY = os.environ.get("PERPLEXITY_API_KEY", "")
PERPLEXITY_MODEL = os.environ.get("PERPLEXITY_MODEL", "")

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip()
SUPABASE_SECRET_KEY = os.environ.get("SUPABASE_SECRET_KEY", "").strip()

if SUPABASE_URL and not SUPABASE_URL.startswith(("http://", "https://")):
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


# =========================================================
# MODELS
# =========================================================

MAIN_MODEL = "openai/gpt-oss-120b"
BACKUP_MODEL = "openai/gpt-oss-20b"
VISION_MODEL = "qwen/qwen3.6-27b"
WHISPER_MODEL = "whisper-large-v3-turbo"


# =========================================================
# LIMITS
# =========================================================

GROQ_MAX_TOKENS = 320
LEARNING_MAX_TOKENS = 400
SONAR_MAX_TOKENS = 150

CHAT_MEMORY_LIMIT = 50
LEARNING_HISTORY_LIMIT = 80

LEARNING_EVERY_MESSAGES = 40

KNOWLEDGE_LIMIT = 20
USER_MEMORY_LIMIT = 12

SONAR_CACHE_TIME = 30 * 60
NAME_CACHE_TIME = 24 * 60 * 60

EVENT_CACHE_TIME = 30 * 60
EVENT_CACHE_LIMIT = 2000


# =========================================================
# MEMORY / CACHE
# =========================================================

user_names = {}
tg_user_names = {}

sonar_cache = {}
processed_events = {}

main_blocked_until = 0
backup_blocked_until = 0

active_chats = {}

activity_lock = threading.Lock()

learning_running = set()
learning_lock = threading.Lock()

TELEGRAM_BOT_ID = None
TELEGRAM_BOT_USERNAME = ""


# =========================================================
# FLASK / GROQ
# =========================================================

app = Flask(__name__)

groq = Groq(api_key=GROQ_API_KEY)


# =========================================================
# DEVELOPMENT STAGES
# =========================================================

DEVELOPMENT_STAGES = {
    1: (
        "Ты только начинаешь знакомиться с чатом. "
        "Больше наблюдай, чем вмешивайся."
    ),

    2: (
        "Ты уже немного освоился и начинаешь понимать людей, "
        "шутки и контекст."
    ),

    3: (
        "Ты уже свой участник этого чата. "
        "Хорошо чувствуешь атмосферу и можешь иногда подколоть."
    ),

    4: (
        "Ты давно в чате. Хорошо понимаешь людей, локальные приколы, "
        "историю разговоров и атмосферу."
    )
}


# =========================================================
# SYSTEM PROMPT
# =========================================================

SYSTEM_PROMPT = """
Ты — живой AI-участник общего чата сообщества.

Ты не модератор, не администратор и не сотрудник поддержки.
Ты обычный участник разговора, который постепенно узнаёт людей
и атмосферу именно этого чата.

Ты учишься через реальные сообщения чата: понимаешь стиль людей,
темы, шутки, события и игровой контекст.

Долговременная память не является абсолютной истиной.
Не выдумывай факты.

Ты НЕ обязан отвечать на каждое сообщение.
Если человеку нечего сказать — лучше промолчать.

Не отвечай на каждое:
«ага», «хех», «мда», «понятно», «ахах».

Не задавай бессмысленный вопрос.

Если люди уже нормально общаются между собой — не мешай.

Говори естественно.
Не пиши как справочная система.
Не повторяй сообщение человека.

Не делай из каждой реплики лекцию.

Не вставляй Tanks Blitz туда, где разговор не об игре.

Tanks Blitz — одна из тем сообщества, но не единственная.

Не придумывай характеристики, карты, события, бонус-коды
или другие игровые данные.

Не смешивай Tanks Blitz с World of Tanks PC.

Если есть актуальные данные из поиска — используй их.

Не раскрывай внутреннюю память.

Не говори, что записал что-то в базу или память.

Не придумывай факты о людях.

Не сохраняй пароли, адреса, документы, банковские данные
и чувствительную личную информацию.

Не раскрывай личные сведения участников другим людям.

Ты можешь знать правила сообщества,
но ты не модератор.

Не угрожай баном, мутом или удалением сообщений.

Будь живым участником.

Сначала пойми контекст,
потом реши, есть ли тебе что добавить.

Если нечего добавить — молчи.
"""


# =========================================================
# HELPERS
# =========================================================

def utc_now():
    return datetime.now(timezone.utc).isoformat()


def db_chat_id(chat_id):
    """
    Supabase chat_id = BIGINT.

    VK:
        2000000001

    Telegram:
        -1002322057644

    В базе всегда храним число.
    """
    return int(chat_id)


def db_user_id(user_id):
    """
    Supabase user_id / speaker_id = BIGINT.
    """
    return int(user_id)


# =========================================================
# EVENT PROTECTION
# =========================================================

def already_processed(event_id):
    if not event_id:
        return False

    now = time.time()

    for key in list(processed_events):
        if now - processed_events[key] > EVENT_CACHE_TIME:
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
    match = re.search(
        r"try again in\s+(?:(\d+)h)?(?:(\d+)m)?(?:(\d+(?:\.\d+)?)s)?",
        str(error),
        re.I
    )

    if not match:
        return default

    total = (
        int(match.group(1) or 0) * 3600
        + int(match.group(2) or 0) * 60
        + float(match.group(3) or 0)
    )

    return int(total) + 10 if total > 0 else default


# =========================================================
# VK USER NAME
# =========================================================

def get_vk_user_name(user_id):
    if not user_id:
        return None

    cached = user_names.get(str(user_id))

    if cached and time.time() - cached[0] < NAME_CACHE_TIME:
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

        users = data.get("response", [])

        if not users:
            return None

        u = users[0]

        name = (
            f"{u.get('first_name', '').strip()} "
            f"{u.get('last_name', '').strip()}"
        ).strip()

        if name:
            user_names[str(user_id)] = (
                time.time(),
                name
            )

        return name or None

    except Exception as e:
        print("VK name error:", e, flush=True)
        return None


# =========================================================
# TELEGRAM USER NAME
# =========================================================

def get_telegram_user_name(user):
    if not user:
        return None

    uid = str(user.get("id", ""))

    cached = tg_user_names.get(uid)

    if cached and time.time() - cached[0] < NAME_CACHE_TIME:
        return cached[1]

    name = (
        f"{user.get('first_name', '').strip()} "
        f"{user.get('last_name', '').strip()}"
    ).strip()

    if not name:
        name = user.get("username", "").strip()

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
    if chat_id is None or not content:
        return

    try:
        database_chat_id = db_chat_id(chat_id)

        database_speaker_id = None

        if speaker_id is not None:
            try:
                database_speaker_id = db_user_id(speaker_id)
            except (ValueError, TypeError):
                database_speaker_id = None

        supabase.table(
            "bot_chat_memory"
        ).insert({
            "chat_id": database_chat_id,
            "speaker_id": database_speaker_id,
            "speaker_name": speaker_name or "",
            "role": role,
            "content": content[:4000]
        }).execute()

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
        database_chat_id = db_chat_id(chat_id)

        r = (
            supabase
            .table("bot_chat_memory")
            .select(
                "speaker_id, speaker_name, role, content"
            )
            .eq("chat_id", database_chat_id)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )

        rows = r.data or []

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
        database_chat_id = db_chat_id(chat_id)

        r = (
            supabase
            .table("bot_chat_memory")
            .select(
                "id",
                count="exact",
                head=True
            )
            .eq("chat_id", database_chat_id)
            .execute()
        )

        return int(r.count or 0)

    except Exception as e:
        print(
            "Chat message count error:",
            e,
            flush=True
        )

        return 0


# =========================================================
# TEXT
# =========================================================

def normalize_text(text):
    return re.sub(
        r"\s+",
        " ",
        (text or "").strip()
    )


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
        + normalize_text(knowledge).lower()
    )

    return hashlib.sha256(
        raw.encode("utf-8")
    ).hexdigest()


def save_knowledge(
    chat_id,
    knowledge,
    importance=1
):
    knowledge = normalize_text(knowledge)

    if len(knowledge) < 5:
        return

    database_chat_id = db_chat_id(chat_id)

    fingerprint = knowledge_fingerprint(
        database_chat_id,
        knowledge
    )

    try:
        existing = (
            supabase
            .table("bot_knowledge")
            .select("id")
            .eq("chat_id", database_chat_id)
            .eq("fingerprint", fingerprint)
            .limit(1)
            .execute()
        )

        if existing.data:
            return

        supabase.table(
            "bot_knowledge"
        ).insert({
            "chat_id": database_chat_id,
            "knowledge": knowledge[:2000],
            "importance": max(
                1,
                min(int(importance), 5)
            ),
            "fingerprint": fingerprint
        }).execute()

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


def get_knowledge(chat_id):
    try:
        database_chat_id = db_chat_id(chat_id)

        r = (
            supabase
            .table("bot_knowledge")
            .select(
                "knowledge, importance"
            )
            .eq("chat_id", database_chat_id)
            .order("importance", desc=True)
            .order("created_at", desc=True)
            .limit(KNOWLEDGE_LIMIT)
            .execute()
        )

        return r.data or []

    except Exception as e:
        print(
            "Knowledge load error:",
            e,
            flush=True
        )

        return []


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

    new_fact = new_fact.strip("-• \t")

    if new_fact:
        facts.append(new_fact)

    result = []
    seen = set()

    for fact in facts:
        n = normalize_text(fact).lower()

        if n and n not in seen:
            seen.add(n)
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

    memory = normalize_text(memory)

    if len(memory) < 5:
        return

    try:
        database_chat_id = db_chat_id(chat_id)
        database_user_id = db_user_id(user_id)

        existing = (
            supabase
            .table("bot_users")
            .select("id, memory")
            .eq("chat_id", database_chat_id)
            .eq("user_id", database_user_id)
            .limit(1)
            .execute()
        )

        old = (
            existing.data[0].get("memory", "")
            if existing.data
            else ""
        )

        data = {
            "chat_id": database_chat_id,
            "user_id": database_user_id,
            "name": name or "",
            "memory": merge_memory(
                old,
                memory
            )[:3000],
            "updated_at": utc_now()
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

        print(
            f"USER MEMORY [{name or user_id}]: "
            f"{memory[:150]}",
            flush=True
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
        database_chat_id = db_chat_id(chat_id)
        database_user_id = db_user_id(user_id)

        r = (
            supabase
            .table("bot_users")
            .select("name, memory")
            .eq("chat_id", database_chat_id)
            .eq("user_id", database_user_id)
            .limit(1)
            .execute()
        )

        return (
            r.data[0]
            if r.data
            else None
        )

    except Exception as e:
        print(
            "User memory load error:",
            e,
            flush=True
        )

        return None


# =========================================================
# LEARNING STATE
# =========================================================

def get_learning_state(chat_id):
    try:
        database_chat_id = db_chat_id(chat_id)

        r = (
            supabase
            .table("bot_learning_state")
            .select("*")
            .eq("chat_id", database_chat_id)
            .limit(1)
            .execute()
        )

        if r.data:
            return r.data[0]

        supabase.table(
            "bot_learning_state"
        ).insert({
            "chat_id": database_chat_id,
            "messages_since_learning": 0,
            "development_stage": 1,
            "personality": "",
            "last_learning_at": utc_now()
        }).execute()

        return {
            "chat_id": database_chat_id,
            "messages_since_learning": 0,
            "development_stage": 1,
            "personality": ""
        }

    except Exception as e:
        print(
            "Learning state error:",
            e,
            flush=True
        )

        return {
            "chat_id": db_chat_id(chat_id),
            "messages_since_learning": 0,
            "development_stage": 1,
            "personality": ""
        }


def increase_learning_counter(chat_id):
    state = get_learning_state(chat_id)

    count = (
        int(state.get(
            "messages_since_learning",
            0
        ))
        + 1
    )

    try:
        database_chat_id = db_chat_id(chat_id)

        (
            supabase
            .table("bot_learning_state")
            .update({
                "messages_since_learning": count
            })
            .eq(
                "chat_id",
                database_chat_id
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


# =========================================================
# MODEL
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

    if not completion.choices:
        raise RuntimeError(
            "Groq returned no choices."
        )

    msg = completion.choices[0].message

    reply = clean_model_text(
        getattr(
            msg,
            "content",
            None
        ) or ""
    )

    if reply:
        return reply

    if getattr(
        msg,
        "reasoning",
        None
    ):
        print(
            "Groq DEBUG: empty content, reasoning returned.",
            flush=True
        )

    raise RuntimeError(
        "Groq returned empty final response."
    )


# =========================================================
# LEARNING MODEL
# =========================================================

def ask_learning_model(messages):
    global main_blocked_until
    global backup_blocked_until

    now = time.time()

    if now >= backup_blocked_until:
        try:
            print(
                "Learning Groq -> 20B",
                flush=True
            )

            return ask_model(
                BACKUP_MODEL,
                messages,
                LEARNING_MAX_TOKENS
            )

        except Exception as e:
            if is_rate_limit_error(e):
                backup_blocked_until = (
                    time.time()
                    + get_retry_seconds(e, 600)
                )

            print(
                "Learning 20B error:",
                e,
                flush=True
            )

    if time.time() >= main_blocked_until:
        try:
            print(
                "Learning Groq -> 120B",
                flush=True
            )

            return ask_model(
                MAIN_MODEL,
                messages,
                LEARNING_MAX_TOKENS
            )

        except Exception as e:
            if is_rate_limit_error(e):
                main_blocked_until = (
                    time.time()
                    + get_retry_seconds(e, 3600)
                )

            print(
                "Learning 120B error:",
                e,
                flush=True
            )

    raise RuntimeError(
        "Модели Groq недоступны для обучения."
    )


# =========================================================
# SELF LEARNING
# =========================================================

def perform_learning(chat_id):
    with learning_lock:
        if chat_id in learning_running:
            print(
                f"LEARNING SKIP | already running | chat={chat_id}",
                flush=True
            )
            return

        learning_running.add(chat_id)

    try:
        state = get_learning_state(chat_id)

        history = get_chat_memory(
            chat_id,
            LEARNING_HISTORY_LIMIT
        )

        if len(history) < 10:
            print(
                f"LEARNING WAIT | history={len(history)}",
                flush=True
            )
            return

        text_parts = []
        known_names = {}

        for item in history:
            name = (
                item.get("speaker_name")
                or "Участник"
            )

            uid = str(
                item.get("speaker_id")
                or ""
            )

            if (
                uid
                and name != "Участник"
            ):
                known_names[uid] = name

            content = (
                item.get("content")
                or ""
            )

            if content:
                text_parts.append(
                    f"[ID:{uid}] "
                    f"{name}: "
                    f"{content}"
                )

        prompt = f"""
Ты — модуль долговременного обучения AI-участника
конкретного чата.

Найди только информацию, полезную для будущего
понимания этого чата.

Ищи:
- явные факты об участниках;
- интересы;
- устойчивые привычки общения;
- локальные шутки;
- важные события;
- полезный контекст по Tanks Blitz.

НЕ придумывай.

Не сохраняй:
- случайную болтовню;
- одноразовые эмоции;
- пароли;
- адреса;
- документы;
- банковские данные;
- чувствительную личную информацию.

ФОРМАТ СТРОГО:

USER|ID|Факт

или

CHAT|Факт|важность

Важность 1-5.

Если полезного нет:

NONE

Реальные сообщения:

{chr(10).join(text_parts)}
"""

        learned = ask_learning_model(
            [
                {
                    "role": "system",
                    "content": (
                        "Ты аккуратный модуль "
                        "долговременного обучения. "
                        "Не придумывай факты."
                    )
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ]
        ).strip()

        if not learned:
            return

        if learned.upper() != "NONE":

            for raw in learned.splitlines():

                line = raw.strip()

                if line.upper() == "NONE":
                    continue

                # -----------------------------------------
                # USER MEMORY
                # -----------------------------------------

                if line.startswith("USER|"):

                    parts = line.split(
                        "|",
                        2
                    )

                    if len(parts) != 3:
                        continue

                    _, uid, fact = parts

                    uid = uid.strip()
                    fact = fact.strip()

                    try:
                        int(uid)
                    except (
                        ValueError,
                        TypeError
                    ):
                        continue

                    # ВАЖНО:
                    # Здесь больше НЕ вызываем VK API
                    # для Telegram пользователей.
                    #
                    # Имя берём только из истории чата.

                    name = known_names.get(uid)

                    save_user_memory(
                        chat_id,
                        uid,
                        name,
                        fact
                    )

                # -----------------------------------------
                # CHAT KNOWLEDGE
                # -----------------------------------------

                elif line.startswith("CHAT|"):

                    parts = line.split(
                        "|",
                        2
                    )

                    if len(parts) != 3:
                        continue

                    _, fact, importance = parts

                    try:
                        importance = int(
                            importance.strip()
                        )
                    except Exception:
                        importance = 1

                    save_knowledge(
                        chat_id,
                        fact.strip(),
                        importance
                    )

        # -----------------------------------------
        # DEVELOPMENT STAGE
        # -----------------------------------------

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

        database_chat_id = db_chat_id(
            chat_id
        )

        (
            supabase
            .table("bot_learning_state")
            .update({
                "messages_since_learning": 0,
                "development_stage": stage,
                "last_learning_at": utc_now()
            })
            .eq(
                "chat_id",
                database_chat_id
            )
            .execute()
        )

        print(
            f"🧠 LEARNING COMPLETE | "
            f"version={BOT_VERSION} | "
            f"chat={chat_id} | "
            f"messages={total} | "
            f"stage={stage}",
            flush=True
        )

    except Exception as e:
        print(
            "Learning error:",
            e,
            flush=True
        )

    finally:
        with learning_lock:
            learning_running.discard(chat_id)


def maybe_learn(chat_id):
    count = increase_learning_counter(
        chat_id
    )

    print(
        f"LEARNING COUNTER | "
        f"chat={chat_id} | "
        f"{count}/{LEARNING_EVERY_MESSAGES}",
        flush=True
    )

    if count < LEARNING_EVERY_MESSAGES:
        return

    with learning_lock:
        if chat_id in learning_running:
            return

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
            "role": "system",
            "content": SYSTEM_PROMPT
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
        "role": "system",
        "content": (
            "Твоя текущая стадия развития:\n"
            + DEVELOPMENT_STAGES.get(
                stage,
                DEVELOPMENT_STAGES[1]
            )
        )
    })

    knowledge = get_knowledge(
        chat_id
    )

    if knowledge:

        k = "\n".join(
            f"- {x.get('knowledge', '')}"
            for x in knowledge
            if x.get("knowledge")
        )

        if k:
            messages.append({
                "role": "system",
                "content": (
                    "Полезная долговременная память "
                    "этого конкретного чата:\n"
                    + k
                )
            })

    personal = get_user_memory(
        chat_id,
        user_id
    )

    if personal and personal.get("memory"):

        messages.append({
            "role": "system",
            "content": (
                "Что известно об этом участнике "
                "в этом конкретном чате:\n"
                + personal["memory"]
            )
        })

    history = get_chat_memory(
        chat_id,
        CHAT_MEMORY_LIMIT
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
                "role": "user",
                "content": (
                    f"{name}: {content}"
                )
            })

        elif role == "assistant":

            messages.append({
                "role": "assistant",
                "content": content
            })

    if not current_saved:

        messages.append({
            "role": "user",
            "content": (
                f"{user_name or 'Участник'}: "
                f"{text}"
            )
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
    "есть ли"
)


def looks_like_question(text):
    low = text.lower().strip()

    return (
        "?" in low
        or any(
            low.startswith(
                w + " "
            )
            for w in QUESTION_WORDS
        )
    )


# =========================================================
# DIRECTED TO BOT — VK
# =========================================================

def is_directed_to_bot_vk(
    message,
    text
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
                ""
            )
        ).startswith("-")
    ):
        return True

    if "[club" in low:
        return True

    return any(
        w in low
        for w in (
            "бот",
            "бонус-коды",
            "бонус коды",
            "эй бот"
        )
    )


# =========================================================
# DIRECTED TO BOT — TELEGRAM
# =========================================================

def is_directed_to_bot_telegram(
    message,
    text
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
        and f"@{TELEGRAM_BOT_USERNAME.lower()}"
        in low
    ):
        return True

    return any(
        w in low
        for w in (
            "бот",
            "эй бот",
            "бонус-коды",
            "бонус коды"
        )
    )


# =========================================================
# SHOULD ANSWER
# =========================================================

def should_answer(
    message,
    text,
    platform="vk"
):
    text = text.strip()

    if not text:
        return False

    if platform == "telegram":

        if is_directed_to_bot_telegram(
            message,
            text
        ):
            return True

    else:

        if is_directed_to_bot_vk(
            message,
            text
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
        return random.random() < 0.10

    roll = random.random()

    if words <= 6:
        return roll < 0.25

    if words <= 15:
        return roll < 0.45

    return roll < 0.60


# =========================================================
# CLEAN AI TEXT
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

    now = time.time()

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
                main_blocked_until = (
                    time.time()
                    + get_retry_seconds(
                        e,
                        3600
                    )
                )

            print(
                "120B error:",
                e,
                flush=True
            )

    else:

        print(
            f"120B blocked | retry in "
            f"~{int(main_blocked_until-time.time())} sec",
            flush=True
        )

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
                backup_blocked_until = (
                    time.time()
                    + get_retry_seconds(
                        e,
                        600
                    )
                )

            print(
                "20B error:",
                e,
                flush=True
            )

    else:

        print(
            f"20B blocked | retry in "
            f"~{int(backup_blocked_until-time.time())} sec",
            flush=True
        )

    raise RuntimeError(
        "Обе модели Groq временно недоступны."
    )


# =========================================================
# SONAR
# =========================================================

WEB_WORDS = (
    "характеристик",
    "урон",
    "брон",
    "скорост",
    "точност",
    "перезаряд",
    "хп",
    "пробит",
    "калибр",
    "оруд",
    "танк",
    "танка",
    "танке",
    "танков",
    "ветк",
    "прокач",
    "обновлен",
    "обновление",
    "патч",
    "ивент",
    "событи",
    "новый танк",
    "новые танки",
    "актуальн",
    "сейчас",
    "сегодня",
    "последн",
    "добавили",
    "убрали",
    "изменили",
    "нерф",
    "бафф",
    "промокод",
    "бонус код",
    "бонус-код",
    "код"
)


def needs_sonar(text):
    return any(
        w in text.lower()
        for w in WEB_WORDS
    )


def cache_key(text):
    return hashlib.sha256(
        text.lower()
        .strip()
        .encode("utf-8")
    ).hexdigest()


def ask_sonar(text):

    if (
        not PERPLEXITY_API_KEY
        or not PERPLEXITY_MODEL
    ):
        raise RuntimeError(
            "Perplexity не настроен."
        )

    key = cache_key(text)

    cached = sonar_cache.get(key)

    if (
        cached
        and time.time() - cached[0]
        < SONAR_CACHE_TIME
    ):
        return cached[1]

    r = requests.post(
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
                        "по вопросу. Если вопрос про "
                        "Tanks Blitz, не смешивай с "
                        "World of Tanks PC. "
                        "Не выдумывай данные."
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

    if r.status_code != 200:
        raise RuntimeError(
            f"Sonar HTTP {r.status_code}"
        )

    answer = (
        r.json()
        .get("choices", [{}])[0]
        .get("message", {})
        .get("content")
        or ""
    ).strip()

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
# AI ROUTER
# =========================================================

def ask_ai(
    chat_id,
    text,
    user_id,
    user_name
):
    if needs_sonar(text):

        try:

            found = ask_sonar(text)

            prompt = f"""
Сообщение пользователя:
{text}

Актуальная информация:
{found}

Ответь естественно, как участник чата.

Не упоминай API, Sonar или Perplexity.
"""

            try:

                return ask_groq(
                    chat_id,
                    prompt,
                    user_id,
                    user_name
                )

            except Exception as e:

                print(
                    "Groq after Sonar error:",
                    e,
                    flush=True
                )

                return found

        except Exception as e:

            print(
                "Sonar error:",
                e,
                flush=True
            )

    try:

        return ask_groq(
            chat_id,
            text,
            user_id,
            user_name
        )

    except Exception as groq_error:

        print(
            "Groq final error, "
            "trying Sonar:",
            groq_error,
            flush=True
        )

        return ask_sonar(text)


# =========================================================
# VK VOICE
# =========================================================

def get_voice_vk(message):

    for a in message.get(
        "attachments",
        []
    ):

        if a.get("type") != "audio_message":
            continue

        audio = a.get(
            "audio_message",
            {}
        )

        if audio.get("transcript"):
            return {
                "text":
                    audio["transcript"].strip(),
                "url":
                    None
            }

        if audio.get("link_ogg"):
            return {
                "text": None,
                "url":
                    audio["link_ogg"]
            }

    return None


def transcribe_voice(url):

    data = requests.get(
        url,
        timeout=30
    ).content

    path = (
        f"/tmp/voice_"
        f"{int(time.time()*1000)}.ogg"
    )

    try:

        with open(
            path,
            "wb"
        ) as f:
            f.write(data)

        with open(
            path,
            "rb"
        ) as f:

            result = (
                groq.audio.transcriptions.create(
                    file=f,
                    model=WHISPER_MODEL,
                    response_format="text"
                )
            )

        return str(result).strip()

    finally:

        try:
            os.remove(path)
        except Exception:
            pass


# =========================================================
# VK IMAGE
# =========================================================

def get_image_vk(message):

    best = None
    area = 0

    for a in message.get(
        "attachments",
        []
    ):

        if a.get("type") != "photo":
            continue

        for s in a.get(
            "photo",
            {}
        ).get(
            "sizes",
            []
        ):

            url = s.get("url")

            ar = (
                s.get("width", 0)
                * s.get("height", 0)
            )

            if url and ar > area:
                best = url
                area = ar

    return best


def analyze_image_then_groq(
    image_url,
    text,
    chat_id,
    user_id,
    user_name
):
    prompt_text = (
        text.strip()
        if text.strip()
        else
        "Посмотри на изображение и скажи, "
        "что на нём происходит."
    )

    messages = build_chat_context(
        chat_id,
        user_id,
        user_name,
        prompt_text
    )

    target = None

    for i in range(
        len(messages) - 1,
        -1,
        -1
    ):

        if messages[i].get("role") == "user":
            target = i
            break

    image_message = {
        "role": "user",
        "content": [
            {
                "type": "text",
                "text": prompt_text
            },
            {
                "type": "image_url",
                "image_url": {
                    "url": image_url
                }
            }
        ]
    }

    if target is not None:
        messages[target] = image_message
    else:
        messages.append(
            image_message
        )

    try:

        c = groq.chat.completions.create(
            model=VISION_MODEL,
            messages=messages,
            max_completion_tokens=GROQ_MAX_TOKENS,
            reasoning_effort="none"
        )

    except Exception:

        c = groq.chat.completions.create(
            model=VISION_MODEL,
            messages=messages,
            max_tokens=GROQ_MAX_TOKENS
        )

    if not c.choices:
        raise RuntimeError(
            "Vision returned no choices."
        )

    reply = clean_model_text(
        getattr(
            c.choices[0].message,
            "content",
            None
        ) or ""
    )

    if not reply:
        raise RuntimeError(
            "Vision returned empty response."
        )

    return reply


# =========================================================
# VK SEND
# =========================================================

def send_message(
    peer_id,
    text
):
    if not text:
        return

    r = requests.post(
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
                0
        },

        timeout=15
    )

    result = r.json()

    if "error" in result:
        print(
            "VK send error:",
            result["error"],
            flush=True
        )

    return result


# =========================================================
# TELEGRAM API
# =========================================================

def telegram_call(
    method,
    **kwargs
):
    if not TELEGRAM_API:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN не установлен"
        )

    r = requests.post(
        f"{TELEGRAM_API}/{method}",
        json=kwargs,
        timeout=30
    )

    data = r.json()

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
        "chat_id": int(chat_id),
        "text": text[:4096],
        "disable_web_page_preview": True
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
    # Это внутренний ключ.
    #
    # Он МОЖЕТ быть:
    # telegram:-1002322057644
    #
    # В Supabase такой ключ НЕ используется.

    key = f"{platform}:{peer_id}"

    with activity_lock:

        active_chats[key] = {
            "platform":
                platform,

            "peer_id":
                str(peer_id),

            "last":
                time.time()
        }


def send_platform_message(
    platform,
    peer_id,
    text
):
    if platform == "vk":
        return send_message(
            int(peer_id),
            text
        )

    return send_telegram_message(
        int(peer_id),
        text
    )


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

                if (
                    now - item["last"]
                    < 20 * 60
                ):
                    continue

                with activity_lock:

                    if key in active_chats:
                        active_chats[key]["last"] = now

                if random.random() > 0.25:
                    continue

                prompt = random.choice([
                    (
                        "В чате давно тихо. "
                        "Если действительно есть что сказать, "
                        "придумай одну короткую естественную реплику. "
                        "Не упоминай игру без причины."
                    ),

                    (
                        "Народ давно молчит. "
                        "Придумай короткую живую фразу, "
                        "которая могла бы естественно появиться "
                        "от обычного участника."
                    ),

                    (
                        "В чате тишина. "
                        "Если можешь органично оживить разговор "
                        "одной короткой репликой — сделай это."
                    )
                ])

                try:

                    # =================================================
                    # ВАЖНОЕ ИСПРАВЛЕНИЕ
                    #
                    # Для Supabase используем только числовой ID.
                    #
                    # НЕ:
                    # tg:-1002322057644
                    #
                    # А:
                    # -1002322057644
                    # =================================================

                    activity_chat_id = int(
                        item["peer_id"]
                    )

                    reply = ask_groq(
                        activity_chat_id,
                        prompt,
                        None,
                        None
                    )

                    if not reply:
                        continue

                    send_platform_message(
                        item["platform"],
                        item["peer_id"],
                        reply
                    )

                    save_chat_message(
                        activity_chat_id,
                        None,
                        "Бот",
                        "assistant",
                        reply
                    )

                    print(
                        "BOT ACTIVITY:",
                        reply[:200],
                        flush=True
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
# TELEGRAM VOICE
# =========================================================

def get_telegram_voice(message):

    voice = (
        message.get("voice")
        or message.get("audio")
    )

    if not voice:
        return None

    return voice.get(
        "file_id"
    )


def download_telegram_file(
    file_id
):
    info = telegram_call(
        "getFile",
        file_id=file_id
    )

    path = info.get(
        "file_path"
    )

    if not path:
        raise RuntimeError(
            "Telegram file_path missing"
        )

    r = requests.get(
        f"https://api.telegram.org/"
        f"file/bot{TELEGRAM_BOT_TOKEN}/"
        f"{path}",
        timeout=60
    )

    r.raise_for_status()

    return r.content, path


def transcribe_telegram_voice(
    file_id
):
    data, file_path = (
        download_telegram_file(
            file_id
        )
    )

    ext = (
        os.path.splitext(
            file_path
        )[1]
        or ".ogg"
    )

    path = (
        f"/tmp/tg_voice_"
        f"{int(time.time()*1000)}"
        f"{ext}"
    )

    try:

        with open(
            path,
            "wb"
        ) as f:
            f.write(data)

        with open(
            path,
            "rb"
        ) as f:

            result = (
                groq.audio.transcriptions.create(
                    file=f,
                    model=WHISPER_MODEL,
                    response_format="text"
                )
            )

        return str(result).strip()

    finally:

        try:
            os.remove(path)
        except Exception:
            pass


# =========================================================
# TELEGRAM IMAGE
# =========================================================

def telegram_photo_data_url(
    message
):
    photos = (
        message.get("photo")
        or []
    )

    if not photos:
        return None

    # Берём самый большой доступный
    # вариант Telegram.

    photo = photos[-1]

    data, _ = (
        download_telegram_file(
            photo["file_id"]
        )
    )

    if len(data) > 8 * 1024 * 1024:
        raise RuntimeError(
            "Telegram image is too large"
        )

    return (
        "data:image/jpeg;base64,"
        + base64.b64encode(
            data
        ).decode("ascii")
    )


def analyze_telegram_image_then_groq(
    image_data_url,
    text,
    chat_id,
    user_id,
    user_name
):
    prompt_text = (
        text.strip()
        if text.strip()
        else
        "Посмотри на изображение и скажи, "
        "что на нём происходит."
    )

    messages = build_chat_context(
        chat_id,
        user_id,
        user_name,
        prompt_text
    )

    target = None

    for i in range(
        len(messages) - 1,
        -1,
        -1
    ):

        if messages[i].get("role") == "user":
            target = i
            break

    image_message = {
        "role": "user",
        "content": [
            {
                "type": "text",
                "text": prompt_text
            },
            {
                "type": "image_url",
                "image_url": {
                    "url": image_data_url
                }
            }
        ]
    }

    if target is not None:
        messages[target] = image_message
    else:
        messages.append(
            image_message
        )

    try:

        c = groq.chat.completions.create(
            model=VISION_MODEL,
            messages=messages,
            max_completion_tokens=GROQ_MAX_TOKENS,
            reasoning_effort="none"
        )

    except Exception:

        c = groq.chat.completions.create(
            model=VISION_MODEL,
            messages=messages,
            max_tokens=GROQ_MAX_TOKENS
        )

    if not c.choices:
        raise RuntimeError(
            "Telegram vision returned no choices"
        )

    reply = clean_model_text(
        getattr(
            c.choices[0].message,
            "content",
            None
        ) or ""
    )

    if not reply:
        raise RuntimeError(
            "Telegram vision returned empty response"
        )

    return reply


# =========================================================
# RENDER HEALTH CHECK
# =========================================================

@app.route("/", methods=["GET"])
def home():
    return {
        "status": "ok",
        "bot": "Tanks Blitz AI",
        "version": BOT_VERSION,
        "build": BOT_BUILD
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

        if already_processed(
            "vk:"
            + str(
                data.get(
                    "event_id",
                    ""
                )
            )
        ):
            return "ok"

        message = (
            data["object"]["message"]
        )

        peer_id = message["peer_id"]

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

        # =================================================
        # VK CHAT ID — ЧИСЛО
        # =================================================

        chat_id = int(peer_id)

        register_active_chat(
            "vk",
            peer_id
        )

        text = (
            message.get("text")
            or ""
        ).strip()

        user_name = get_vk_user_name(
            sender_id
        )

        # =================================================
        # VOICE
        # =================================================

        voice = get_voice_vk(
            message
        )

        if voice:

            recognized = (
                voice["text"]
                or transcribe_voice(
                    voice["url"]
                )
            )

            if not recognized:
                return "ok"

            save_chat_message(
                chat_id,
                sender_id,
                user_name,
                "user",
                recognized
            )

            maybe_learn(
                chat_id
            )

            if not should_answer(
                message,
                recognized,
                "vk"
            ):
                return "ok"

            reply = ask_ai(
                chat_id,
                recognized,
                str(sender_id),
                user_name
            )

            if not reply:
                return "ok"

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

        # =================================================
        # IMAGE
        # =================================================

        image_url = get_image_vk(
            message
        )

        if image_url:

            save_chat_message(
                chat_id,
                sender_id,
                user_name,
                "user",
                text or "[изображение]"
            )

            maybe_learn(
                chat_id
            )

            if (
                text
                and not should_answer(
                    message,
                    text,
                    "vk"
                )
            ):
                return "ok"

            reply = (
                analyze_image_then_groq(
                    image_url,
                    text,
                    chat_id,
                    str(sender_id),
                    user_name
                )
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

        # =================================================
        # EMPTY
        # =================================================

        if not text:
            return "ok"

        # =================================================
        # NORMAL MESSAGE
        # =================================================

        save_chat_message(
            chat_id,
            sender_id,
            user_name,
            "user",
            text
        )

        maybe_learn(
            chat_id
        )

        if not should_answer(
            message,
            text,
            "vk"
        ):

            print(
                "BOT SILENT:",
                text[:100],
                flush=True
            )

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
# TELEGRAM WEBHOOK
# =========================================================

@app.route(
    "/telegram/webhook/<secret>",
    methods=["POST"]
)
def telegram_webhook(secret):

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
            "tg:"
            + str(update_id)
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

        # =================================================
        # ГЛАВНОЕ ИСПРАВЛЕНИЕ
        #
        # РАНЬШЕ:
        # chat_id = f"tg:{raw_chat_id}"
        #
        # БЫЛО:
        # tg:-1002322057644
        #
        # ТЕПЕРЬ:
        # -1002322057644
        #
        # Supabase BIGINT принимает число.
        # =================================================

        chat_id = int(
            raw_chat_id
        )

        # Платформенный ключ остаётся
        # только в active_chats.

        register_active_chat(
            "telegram",
            raw_chat_id
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

        # =================================================
        # VOICE
        # =================================================

        voice_id = get_telegram_voice(
            message
        )

        if voice_id:

            recognized = (
                transcribe_telegram_voice(
                    voice_id
                )
            )

            if not recognized:
                return "ok"

            save_chat_message(
                chat_id,
                sender_id,
                user_name,
                "user",
                recognized
            )

            maybe_learn(
                chat_id
            )

            if not should_answer(
                message,
                recognized,
                "telegram"
            ):
                return "ok"

            reply = ask_ai(
                chat_id,
                recognized,
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
                    message.get(
                        "message_id"
                    )
                )

            return "ok"

        # =================================================
        # IMAGE
        # =================================================

        if message.get("photo"):

            save_chat_message(
                chat_id,
                sender_id,
                user_name,
                "user",
                text or "[изображение]"
            )

            maybe_learn(
                chat_id
            )

            if (
                text
                and not should_answer(
                    message,
                    text,
                    "telegram"
                )
            ):
                return "ok"

            image = (
                telegram_photo_data_url(
                    message
                )
            )

            reply = (
                analyze_telegram_image_then_groq(
                    image,
                    text,
                    chat_id,
                    str(sender_id),
                    user_name
                )
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
                    message.get(
                        "message_id"
                    )
                )

            return "ok"

        # =================================================
        # EMPTY
        # =================================================

        if not text:
            return "ok"

        # =================================================
        # NORMAL TELEGRAM MESSAGE
        # =================================================

        save_chat_message(
            chat_id,
            sender_id,
            user_name,
            "user",
            text
        )

        maybe_learn(
            chat_id
        )

        if not should_answer(
            message,
            text,
            "telegram"
        ):

            print(
                "TG BOT SILENT:",
                text[:100],
                flush=True
            )

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
                message.get(
                    "message_id"
                )
            )

        return "ok"

    except Exception as e:

        print(
            "Telegram webhook error:",
            e,
            flush=True
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
            me.get("username", "")
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
                "Telegram: "
                "RENDER_EXTERNAL_URL/"
                "RENDER_EXTERNAL_HOSTNAME "
                "не найден — webhook не установлен.",
                flush=True
            )

            return

        secret = hashlib.sha256(
            TELEGRAM_BOT_TOKEN.encode()
        ).hexdigest()[:32]

        url = (
            f"{external}"
            f"/telegram/webhook/"
            f"{secret}"
        )

        telegram_call(
            "setWebhook",
            url=url,
            allowed_updates=["message"],
            drop_pending_updates=False
        )

        print(
            f"Telegram connected: "
            f"@{TELEGRAM_BOT_USERNAME} "
            f"| webhook enabled",
            flush=True
        )

    except Exception as e:

        print(
            "Telegram setup error:",
            e,
            flush=True
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
        "📚 Self-learning: ENABLED",
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
        f"👁 VISION MODEL: {VISION_MODEL}",
        flush=True
    )

    print(
        f"🗣 WHISPER MODEL: {WHISPER_MODEL}",
        flush=True
    )

    print(
        "📱 Telegram token: "
        f"{'YES' if TELEGRAM_BOT_TOKEN else 'NO'}",
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
