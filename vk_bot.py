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
# BOT VERSION
# =========================================================

BOT_VERSION = "V1.3"
BOT_BUILD = "Начальное самообучение"


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


# =========================================================
# SUPABASE
# =========================================================

supabase = create_client(
    SUPABASE_URL,
    SUPABASE_SECRET_KEY
)

print(
    f"🤖 Бот {BOT_VERSION} — {BOT_BUILD}",
    flush=True
)

print(
    "Supabase подключён:",
    bool(supabase),
    flush=True
)


# =========================================================
# VK / MODELS
# =========================================================

VK_API = "https://api.vk.com/method"
VK_VERSION = "5.199"

MAIN_MODEL = "openai/gpt-oss-120b"
BACKUP_MODEL = "openai/gpt-oss-20b"
VISION_MODEL = "qwen/qwen3.6-27b"
WHISPER_MODEL = "whisper-large-v3-turbo"


# =========================================================
# LIMITS
# =========================================================

GROQ_MAX_TOKENS = 220
LEARNING_MAX_TOKENS = 350
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
# GLOBAL CACHE
# =========================================================

user_names = {}
sonar_cache = {}
processed_events = {}

main_blocked_until = 0
backup_blocked_until = 0

active_chats = {}

activity_lock = threading.Lock()

learning_running = set()
learning_lock = threading.Lock()


# =========================================================
# APP
# =========================================================

app = Flask(__name__)

groq = Groq(
    api_key=GROQ_API_KEY
)


# =========================================================
# DEVELOPMENT
# =========================================================

DEVELOPMENT_STAGES = {

    1:
        "Ты только начинаешь знакомиться с чатом. "
        "Больше наблюдай, чем вмешивайся.",

    2:
        "Ты уже немного освоился и начинаешь понимать "
        "людей, шутки и контекст.",

    3:
        "Ты уже свой участник этого чата. "
        "Хорошо чувствуешь атмосферу и можешь иногда подколоть.",

    4:
        "Ты давно в чате. Хорошо понимаешь людей, "
        "локальные приколы, историю разговоров и атмосферу."
}


# =========================================================
# SYSTEM PROMPT
# =========================================================

SYSTEM_PROMPT = """
Ты — живой AI-участник общего чата сообщества.

Ты не модератор.
Ты не администратор.
Ты не сотрудник поддержки.

Ты обычный участник разговора, который постепенно
узнаёт людей и атмосферу именно этого чата.

==================================================
ГЛАВНАЯ ИДЕЯ
==================================================

Ты учишься через реальные сообщения чата.

Постепенно ты можешь понимать:

- кто как общается;
- кто с кем дружит;
- какие темы людям интересны;
- какие шутки повторяются;
- какие события происходили в чате;
- какие игровые и неигровые темы обсуждают;
- какой стиль общения принят.

Долговременная память помогает понимать контекст,
но не является абсолютной истиной.

Если информация сомнительная — не выдавай её
как установленный факт.

==================================================
ОБЫЧНОЕ ОБЩЕНИЕ
==================================================

Ты НЕ обязан отвечать на каждое сообщение.

Если человеку нечего сказать — лучше промолчать.

Не надо отвечать на каждое:

«ага»
«хех»
«мда»
«понятно»
«ахах»

Не задавай бессмысленный вопрос только ради
продолжения разговора.

Не пытайся постоянно искусственно оживлять чат.

Если люди уже нормально общаются между собой —
не мешай.

==================================================
СТИЛЬ
==================================================

Говори естественно.

Не пиши как справочная система.

Не используй постоянно:

«Конечно»
«Разумеется»
«Понимаю»
«Хороший вопрос»
«Если хочешь, могу...»

Не повторяй сообщение человека.

Не делай из каждой реплики лекцию.

Не вставляй Tanks Blitz туда, где разговор
вообще не об игре.

Если люди обсуждают деньги — обсуждай деньги.

Если шутят — можешь поддержать шутку.

Если обсуждают жизнь — разговаривай о жизни.

Если обсуждают игру — разговаривай об игре.

==================================================
TANKS BLITZ
==================================================

Tanks Blitz — одна из тем сообщества,
но НЕ единственная тема разговора.

Не надо постоянно возвращать разговор к игре.

Не придумывай характеристики танков, карты,
события, бонус-коды или другие игровые данные.

Не смешивай Tanks Blitz с World of Tanks для ПК.

Если есть актуальные данные из поиска —
используй их.

==================================================
ПАМЯТЬ
==================================================

Есть два типа долговременной памяти:

1. Память конкретного чата.
2. Память конкретного участника внутри конкретного чата.

Не смешивай разные чаты.

Если один человек находится в нескольких чатах,
его память в них может отличаться.

Не рассказывай человеку о внутренней памяти.

Не говори:

«Я записал это в память».
«Я тебя запомнил в базе».
«Моя система сохранила...»

==================================================
ЛЮДИ
==================================================

Не придумывай факты о людях.

Не сохраняй:

- пароли;
- адреса;
- документы;
- банковские данные;
- чувствительную личную информацию.

Не раскрывай личные сведения участников другим людям.

Не вытаскивай старую информацию без причины.

Используй имена естественно и не слишком часто.

==================================================
ПРАВИЛА ЧАТА
==================================================

Ты можешь знать правила сообщества,
но ты не модератор.

Никогда не говори:

«Я тебя забаню».
«Я тебя замучу».
«Я удалю сообщение».

Можно спокойно напомнить о правилах,
если это действительно уместно.

==================================================
ГЛАВНОЕ
==================================================

Будь живым участником.

Не пытайся доказать, что ты умный.

Не отвечай ради самого факта ответа.

Сначала пойми контекст.

Потом реши, есть ли тебе что добавить.

Если нечего добавить — молчи.
"""


# =========================================================
# TIME
# =========================================================

def utc_now():
    return datetime.now(timezone.utc).isoformat()


# =========================================================
# EVENT PROTECTION
# =========================================================

def already_processed(event_id):

    if not event_id:
        return False

    now = time.time()

    expired = [
        key
        for key, saved in processed_events.items()
        if now - saved > EVENT_CACHE_TIME
    ]

    for key in expired:
        processed_events.pop(key, None)

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

    return (
        int(total) + 10
        if total > 0
        else default
    )


# =========================================================
# VK NAME
# =========================================================

def get_vk_user_name(user_id):

    if not user_id:
        return None

    cached = user_names.get(
        str(user_id)
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

        user_names[str(user_id)] = (
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
# CHAT MEMORY
# =========================================================

def save_chat_message(
    chat_id,
    speaker_id,
    speaker_name,
    role,
    content
):

    if not chat_id or not content:
        return

    try:

        # BIGINT:
        # пользователь = числовой VK ID
        # бот / system = NULL
        if speaker_id is None:
            database_speaker_id = None
        else:
            try:
                database_speaker_id = int(
                    speaker_id
                )
            except (ValueError, TypeError):
                database_speaker_id = None

        supabase.table(
            "bot_chat_memory"
        ).insert({

            "chat_id":
                str(chat_id),

            "speaker_id":
                database_speaker_id,

            "speaker_name":
                speaker_name or "",

            "role":
                role,

            "content":
                content[:4000]

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

        response = (
            supabase
            .table("bot_chat_memory")
            .select(
                "speaker_id, speaker_name, role, content"
            )
            .eq(
                "chat_id",
                str(chat_id)
            )
            .order(
                "created_at",
                desc=True
            )
            .limit(limit)
            .execute()
        )

        rows = response.data or []

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

        response = (
            supabase
            .table("bot_chat_memory")
            .select(
                "id",
                count="exact",
                head=True
            )
            .eq(
                "chat_id",
                str(chat_id)
            )
            .execute()
        )

        return int(
            response.count or 0
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

def normalize_text(text):

    return re.sub(
        r"\s+",
        " ",
        (text or "").strip()
    )


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

    fingerprint = knowledge_fingerprint(
        chat_id,
        knowledge
    )

    try:

        existing = (
            supabase
            .table("bot_knowledge")
            .select("id")
            .eq(
                "chat_id",
                str(chat_id)
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

        supabase.table(
            "bot_knowledge"
        ).insert({

            "chat_id":
                str(chat_id),

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

        response = (
            supabase
            .table("bot_knowledge")
            .select(
                "knowledge, importance"
            )
            .eq(
                "chat_id",
                str(chat_id)
            )
            .order(
                "importance",
                desc=True
            )
            .order(
                "created_at",
                desc=True
            )
            .limit(KNOWLEDGE_LIMIT)
            .execute()
        )

        return response.data or []

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

        for line in old_memory.splitlines():

            line = line.strip(
                "-• \t"
            )

            if line:
                facts.append(line)

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

        if not normalized:
            continue

        if normalized in seen:
            continue

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

    if not chat_id or not user_id or not memory:
        return

    memory = normalize_text(
        memory
    )

    if len(memory) < 5:
        return

    try:

        existing = (
            supabase
            .table("bot_users")
            .select(
                "id, memory"
            )
            .eq(
                "chat_id",
                str(chat_id)
            )
            .eq(
                "user_id",
                str(user_id)
            )
            .limit(1)
            .execute()
        )

        old_memory = ""

        if existing.data:

            old_memory = (
                existing.data[0].get(
                    "memory"
                )
                or ""
            )

        merged = merge_memory(
            old_memory,
            memory
        )

        data = {

            "chat_id":
                str(chat_id),

            "user_id":
                str(user_id),

            "name":
                name or "",

            "memory":
                merged[:3000],

            "updated_at":
                utc_now()
        }

        if existing.data:

            supabase.table(
                "bot_users"
            ).update(data).eq(
                "id",
                existing.data[0]["id"]
            ).execute()

        else:

            supabase.table(
                "bot_users"
            ).insert(data).execute()

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

    if not user_id:
        return None

    try:

        response = (
            supabase
            .table("bot_users")
            .select(
                "name, memory"
            )
            .eq(
                "chat_id",
                str(chat_id)
            )
            .eq(
                "user_id",
                str(user_id)
            )
            .limit(1)
            .execute()
        )

        if not response.data:
            return None

        return response.data[0]

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

        response = (
            supabase
            .table("bot_learning_state")
            .select("*")
            .eq(
                "chat_id",
                str(chat_id)
            )
            .limit(1)
            .execute()
        )

        if response.data:
            return response.data[0]

        supabase.table(
            "bot_learning_state"
        ).insert({

            "chat_id":
                str(chat_id),

            "messages_since_learning":
                0,

            "development_stage":
                1,

            "personality":
                "",

            "last_learning_at":
                utc_now()

        }).execute()

        return {

            "chat_id":
                str(chat_id),

            "messages_since_learning":
                0,

            "development_stage":
                1,

            "personality":
                ""
        }

    except Exception as e:

        print(
            "Learning state error:",
            e,
            flush=True
        )

        return {

            "chat_id":
                str(chat_id),

            "messages_since_learning":
                0,

            "development_stage":
                1,

            "personality":
                ""
        }


def increase_learning_counter(
    chat_id
):

    state = get_learning_state(
        chat_id
    )

    count = (
        int(
            state.get(
                "messages_since_learning",
                0
            )
        )
        + 1
    )

    try:

        supabase.table(
            "bot_learning_state"
        ).update({

            "messages_since_learning":
                count

        }).eq(
            "chat_id",
            str(chat_id)
        ).execute()

    except Exception as e:

        print(
            "Learning counter error:",
            e,
            flush=True
        )

    return count


# =========================================================
# REAL LEARNING
# =========================================================

def perform_learning(chat_id):

    with learning_lock:

        if chat_id in learning_running:

            print(
                f"LEARNING SKIP | already running | "
                f"chat={chat_id}",
                flush=True
            )

            return

        learning_running.add(
            chat_id
        )

    try:

        state = get_learning_state(
            chat_id
        )

        history = get_chat_memory(
            chat_id,
            LEARNING_HISTORY_LIMIT
        )

        if len(history) < 10:

            print(
                f"LEARNING WAIT | "
                f"history={len(history)}",
                flush=True
            )

            return

        text_parts = []

        for item in history:

            name = (
                item.get(
                    "speaker_name"
                )
                or "Участник"
            )

            user_id = (
                item.get(
                    "speaker_id"
                )
                or ""
            )

            content = (
                item.get(
                    "content"
                )
                or ""
            )

            if not content:
                continue

            text_parts.append(
                f"[ID:{user_id}] "
                f"{name}: {content}"
            )

        conversation = "\n".join(
            text_parts
        )

        prompt = f"""
Ты — модуль долговременного обучения
AI-участника конкретного чата.

Перед тобой реальные сообщения одного чата.

Твоя задача — НЕ пересказывать разговор.

Найди только информацию, которую действительно
полезно сохранить для будущего понимания этого чата.

Ищи:

1. Явные факты о конкретных участниках.
2. Интересы участников.
3. Устойчивые привычки общения.
4. Повторяющиеся локальные шутки.
5. Важные события внутри чата.
6. Полезный контекст для будущих разговоров.
7. Полезные знания по Tanks Blitz,
   если они действительно появились из разговора.

НЕ сохраняй:

- случайную болтовню;
- одноразовые сообщения;
- обычные эмоции;
- оскорбления как факты;
- пароли;
- адреса;
- документы;
- банковские данные;
- чувствительную личную информацию.

НЕ ПРИДУМЫВАЙ.

Не превращай каждую реплику в знание.

ФОРМАТ ОТВЕТА СТРОГО:

USER|ID|Факт

или:

CHAT|Факт|важность

Важность от 1 до 5.

Если полезной информации нет:

NONE

Реальные сообщения:

{conversation}
"""

        learned = ask_model(
            BACKUP_MODEL,
            [
                {
                    "role":
                        "system",

                    "content":
                        "Ты аккуратный модуль "
                        "долговременного обучения. "
                        "Не придумывай факты."
                },

                {
                    "role":
                        "user",

                    "content":
                        prompt
                }
            ],
            max_tokens=
                LEARNING_MAX_TOKENS
        )

        learned = (
            learned or ""
        ).strip()

        if not learned:
            return

        if learned.upper() == "NONE":

            print(
                "LEARNING: nothing new",
                flush=True
            )

        else:

            for raw_line in learned.splitlines():

                line = raw_line.strip()

                if not line:
                    continue

                if line.upper() == "NONE":
                    continue

                # -----------------------------------------
                # USER FACT
                # -----------------------------------------

                if line.startswith(
                    "USER|"
                ):

                    parts = line.split(
                        "|",
                        2
                    )

                    if len(parts) != 3:
                        continue

                    _,
                    user_id,
                    fact = parts

                    user_id = (
                        user_id.strip()
                    )

                    fact = (
                        fact.strip()
                    )

                    if not user_id or not fact:
                        continue

                    try:
                        int(user_id)
                    except (ValueError, TypeError):
                        continue

                    name = get_vk_user_name(
                        user_id
                    )

                    save_user_memory(
                        chat_id,
                        user_id,
                        name,
                        fact
                    )

                # -----------------------------------------
                # CHAT KNOWLEDGE
                # -----------------------------------------

                elif line.startswith(
                    "CHAT|"
                ):

                    parts = line.split(
                        "|",
                        2
                    )

                    if len(parts) != 3:
                        continue

                    _,
                    fact,
                    importance = parts

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

        # =================================================
        # DEVELOPMENT STAGE
        # =================================================

        stage = int(
            state.get(
                "development_stage",
                1
            )
        )

        total_messages = (
            get_chat_message_count(
                chat_id
            )
        )

        if stage < 2 and total_messages >= 300:
            stage = 2

        if stage < 3 and total_messages >= 1000:
            stage = 3

        if stage < 4 and total_messages >= 3000:
            stage = 4

        supabase.table(
            "bot_learning_state"
        ).update({

            "messages_since_learning":
                0,

            "development_stage":
                stage,

            "last_learning_at":
                utc_now()

        }).eq(
            "chat_id",
            str(chat_id)
        ).execute()

        print(
            f"🧠 LEARNING COMPLETE | "
            f"version={BOT_VERSION} | "
            f"chat={chat_id} | "
            f"messages={total_messages} | "
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

            learning_running.discard(
                chat_id
            )


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

    try:

        supabase.table(
            "bot_learning_state"
        ).update({

            "messages_since_learning":
                0

        }).eq(
            "chat_id",
            str(chat_id)
        ).execute()

    except Exception as e:

        print(
            "Learning pre-reset error:",
            e,
            flush=True
        )

    thread = threading.Thread(
        target=perform_learning,
        args=(chat_id,),
        daemon=True
    )

    thread.start()


# =========================================================
# CONTEXT
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
            "Твоя текущая стадия развития:\n"
            +
            DEVELOPMENT_STAGES.get(
                stage,
                DEVELOPMENT_STAGES[1]
            )
    })

    # =====================================================
    # CHAT KNOWLEDGE
    # =====================================================

    knowledge = get_knowledge(
        chat_id
    )

    if knowledge:

        knowledge_text = "\n".join(

            f"- {item.get('knowledge', '')}"

            for item in knowledge

            if item.get("knowledge")
        )

        if knowledge_text:

            messages.append({

                "role":
                    "system",

                "content":
                    "Полезная долговременная "
                    "память этого конкретного чата.\n"
                    "Используй её только если она "
                    "относится к текущему разговору:\n"
                    +
                    knowledge_text
            })

    # =====================================================
    # USER MEMORY
    # =====================================================

    personal = get_user_memory(
        chat_id,
        user_id
    )

    if personal and personal.get(
        "memory"
    ):

        messages.append({

            "role":
                "system",

            "content":
                "Что известно об этом участнике "
                "в этом конкретном чате:\n"
                +
                personal["memory"]
        })

    # =====================================================
    # RECENT CHAT
    # =====================================================

    history = get_chat_memory(
        chat_id,
        CHAT_MEMORY_LIMIT
    )

    current_already_saved = False

    for item in history:

        role = item.get(
            "role"
        )

        content = (
            item.get(
                "content"
            )
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

        speaker_id = str(
            item.get(
                "speaker_id"
            )
            or ""
        )

        if (
            role == "user"
            and speaker_id == str(user_id)
            and content == text
        ):

            current_already_saved = True

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

    if not current_already_saved:

        messages.append({

            "role":
                "user",

            "content":
                f"{user_name or 'Участник'}: {text}"
        })

    return messages


# =========================================================
# RESPONSE BRAIN
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

    if "?" in low:
        return True

    return any(
        low.startswith(
            word + " "
        )
        for word in QUESTION_WORDS
    )


def is_directed_to_bot(
    message,
    text
):

    low = text.lower()

    reply_message = message.get(
        "reply_message"
    )

    if reply_message:

        reply_from = (
            reply_message.get(
                "from_id"
            )
        )

        if reply_from:

            if str(
                reply_from
            ).startswith("-"):

                return True

    if "[club" in low:
        return True

    bot_words = (
        "бот",
        "бонус-коды",
        "бонус коды",
        "эй бот"
    )

    return any(
        word in low
        for word in bot_words
    )


def should_answer(
    message,
    text
):

    text = text.strip()

    if not text:
        return False

    if len(text) <= 1:
        return False

    if is_directed_to_bot(
        message,
        text
    ):
        return True

    if looks_like_question(
        text
    ):
        return True

    short_words = len(
        text.split()
    )

    if short_words <= 2:

        return random.random() < 0.10

    roll = random.random()

    if short_words <= 6:

        return roll < 0.25

    if short_words <= 15:

        return roll < 0.45

    return roll < 0.60


# =========================================================
# GROQ
# =========================================================

def ask_model(
    model,
    messages,
    max_tokens=GROQ_MAX_TOKENS
):

    completion = (
        groq
        .chat
        .completions
        .create(
            model=model,
            messages=messages,
            max_tokens=max_tokens
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

    reply = re.sub(
        r"<think>.*?</think>",
        "",
        reply,
        flags=
            re.DOTALL
            | re.IGNORECASE
    ).strip()

    reply = re.sub(
        r"<think>.*$",
        "",
        reply,
        flags=
            re.DOTALL
            | re.IGNORECASE
    ).strip()

    return reply


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

    low = text.lower()

    return any(
        word in low
        for word in WEB_WORDS
    )


def cache_key(text):

    return hashlib.sha256(
        text.lower()
        .strip()
        .encode("utf-8")
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
            return answer

    response = requests.post(

        "https://api.perplexity.ai/"
        "chat/completions",

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
                    "role":
                        "system",

                    "content":
                        "Найди актуальную информацию "
                        "по вопросу пользователя. "
                        "Если вопрос про Tanks Blitz, "
                        "не смешивай её с World of Tanks PC. "
                        "Не выдумывай данные."
                },

                {
                    "role":
                        "user",

                    "content":
                        text
                }
            ],

            "max_tokens":
                SONAR_MAX_TOKENS
        },

        timeout=30
    )

    if response.status_code != 200:

        raise RuntimeError(
            f"Sonar HTTP "
            f"{response.status_code}"
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


def ask_ai(
    chat_id,
    text,
    user_id,
    user_name
):

    if needs_sonar(text):

        try:

            found = ask_sonar(
                text
            )

            prompt = (
                f"Сообщение пользователя:\n"
                f"{text}\n\n"
                f"Актуальная информация:\n"
                f"{found}\n\n"
                "Ответь естественно, как участник "
                "чата. Не упоминай API, Sonar "
                "или Perplexity."
            )

            return ask_groq(
                chat_id,
                prompt,
                user_id,
                user_name
            )

        except Exception as e:

            print(
                "Sonar error:",
                e,
                flush=True
            )

    return ask_groq(
        chat_id,
        text,
        user_id,
        user_name
    )


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

    data = requests.get(
        url,
        timeout=30
    ).content

    path = (
        f"/tmp/voice_"
        f"{int(time.time() * 1000)}.ogg"
    )

    try:

        with open(
            path,
            "wb"
        ) as file:

            file.write(data)

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

        try:
            os.remove(path)
        except Exception:
            pass


# =========================================================
# IMAGE
# =========================================================

def get_image(message):

    best = None
    best_area = 0

    for attachment in message.get(
        "attachments",
        []
    ):

        if attachment.get(
            "type"
        ) != "photo":

            continue

        photo = attachment.get(
            "photo",
            {}
        )

        for size in photo.get(
            "sizes",
            []
        ):

            url = size.get(
                "url"
            )

            if not url:
                continue

            area = (
                size.get("width", 0)
                *
                size.get("height", 0)
            )

            if area > best_area:

                best = url
                best_area = area

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

    target_index = None

    for index in range(
        len(messages) - 1,
        -1,
        -1
    ):

        if messages[index].get(
            "role"
        ) == "user":

            target_index = index
            break

    image_message = {

        "role":
            "user",

        "content": [

            {
                "type":
                    "text",

                "text":
                    prompt_text
            },

            {
                "type":
                    "image_url",

                "image_url": {
                    "url":
                        image_url
                }
            }
        ]
    }

    if target_index is not None:

        messages[
            target_index
        ] = image_message

    else:

        messages.append(
            image_message
        )

    completion = (
        groq
        .chat
        .completions
        .create(
            model=VISION_MODEL,
            messages=messages,
            max_tokens=GROQ_MAX_TOKENS
        )
    )

    if not completion.choices:

        raise RuntimeError(
            "Vision returned no choices."
        )

    reply = (
        completion
        .choices[0]
        .message
        .content
    )

    if not reply:

        raise RuntimeError(
            "Vision returned empty response."
        )

    reply = re.sub(
        r"<think>.*?</think>",
        "",
        reply,
        flags=
            re.DOTALL
            | re.IGNORECASE
    ).strip()

    reply = re.sub(
        r"<think>.*$",
        "",
        reply,
        flags=
            re.DOTALL
            | re.IGNORECASE
    ).strip()

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

    response = requests.post(

        f"{VK_API}/messages.send",

        data={

            "access_token":
                VK_TOKEN,

            "v":
                VK_VERSION,

            "peer_id":
                peer_id,

            "message":
                text[:4096],

            "random_id":
                0
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
# ACTIVE CHAT
# =========================================================

def register_active_chat(
    peer_id
):

    with activity_lock:

        active_chats[
            str(peer_id)
        ] = time.time()


# =========================================================
# SELF INITIATED CHAT
# =========================================================

def activity_loop():

    while True:

        try:

            now = time.time()

            with activity_lock:

                chats = dict(
                    active_chats
                )

            for chat_id, last_message in chats.items():

                if (
                    now - last_message
                    < 20 * 60
                ):
                    continue

                with activity_lock:

                    active_chats[
                        chat_id
                    ] = now

                if random.random() > 0.25:
                    continue

                prompts = [

                    "В чате давно тихо. "
                    "Если действительно есть что сказать, "
                    "придумай одну короткую естественную реплику. "
                    "Не упоминай игру без причины.",

                    "Народ давно молчит. "
                    "Придумай короткую живую фразу, "
                    "которая могла бы естественно появиться "
                    "от обычного участника.",

                    "В чате тишина. "
                    "Если можешь органично оживить разговор "
                    "одной короткой репликой — сделай это."
                ]

                prompt = random.choice(
                    prompts
                )

                try:

                    reply = ask_groq(
                        chat_id,
                        prompt,
                        None,
                        None
                    )

                    if not reply:
                        continue

                    send_message(
                        int(chat_id),
                        reply
                    )

                    save_chat_message(
                        chat_id,
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
        # DUPLICATE EVENT
        # =================================================

        if already_processed(
            data.get("event_id")
        ):

            return "ok"

        message = data[
            "object"
        ][
            "message"
        ]

        peer_id = message[
            "peer_id"
        ]

        sender_id = (
            message.get("from_id")
            or message.get("user_id")
        )

        # =================================================
        # PRIVATE MESSAGES
        # =================================================

        if (
            sender_id
            and int(peer_id)
            == int(sender_id)
        ):

            print(
                "Private message ignored.",
                flush=True
            )

            return "ok"

        if not sender_id:
            return "ok"

        chat_id = str(
            peer_id
        )

        register_active_chat(
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

        voice = get_voice(
            message
        )

        if voice:

            if voice["text"]:

                recognized = (
                    voice["text"]
                )

            else:

                try:

                    recognized = (
                        transcribe_voice(
                            voice["url"]
                        )
                    )

                except Exception as e:

                    print(
                        "Whisper error:",
                        e,
                        flush=True
                    )

                    return "ok"

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
                recognized
            ):

                return "ok"

            try:

                reply = ask_ai(
                    chat_id,
                    recognized,
                    str(sender_id),
                    user_name
                )

            except Exception as e:

                print(
                    "AI error:",
                    e,
                    flush=True
                )

                return "ok"

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

        image_url = get_image(
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
                    text
                )
            ):

                return "ok"

            try:

                reply = (
                    analyze_image_then_groq(
                        image_url,
                        text,
                        chat_id,
                        str(sender_id),
                        user_name
                    )
                )

            except Exception as e:

                print(
                    "Image error:",
                    e,
                    flush=True
                )

                return "ok"

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
        # EMPTY
        # =================================================

        if not text:
            return "ok"

        # =================================================
        # SAVE EVERY MESSAGE
        # =================================================

        save_chat_message(
            chat_id,
            sender_id,
            user_name,
            "user",
            text
        )

        # =================================================
        # LEARNING
        # =================================================

        maybe_learn(
            chat_id
        )

        # =================================================
        # BRAIN
        # =================================================

        if not should_answer(
            message,
            text
        ):

            print(
                "BOT SILENT:",
                text[:100],
                flush=True
            )

            return "ok"

        # =================================================
        # AI
        # =================================================

        try:

            reply = ask_ai(
                chat_id,
                text,
                str(sender_id),
                user_name
            )

        except Exception as e:

            print(
                "AI error:",
                e,
                flush=True
            )

            return "ok"

        if not reply:
            return "ok"

        # =================================================
        # SAVE BOT MESSAGE
        # =================================================

        save_chat_message(
            chat_id,
            None,
            "Бот",
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
        "========================================",
        flush=True
    )

    activity_thread = threading.Thread(
        target=activity_loop,
        daemon=True
    )

    activity_thread.start()

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
