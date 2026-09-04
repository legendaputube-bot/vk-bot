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
    "Самообучение + защита участников + "
    "официальная память Tanks Blitz + "
    "VK + Telegram + OpenRouter"
)

# ---------------------------------------------------------
# ADMIN / TESTER
# ---------------------------------------------------------

# Главный администратор.
# Права определяются ТОЛЬКО по реальному VK ID.
ADMIN_ID = 948950706

# Тестер.
# Пока тестером является только главный администратор.
# Позже сюда можно добавить другие реальные VK ID.
TESTER_IDS = {
    ADMIN_ID
}

ADMIN_NICK = "Blitz"

# Защита от слишком частых вмешательств бота
INTERVENTION_COOLDOWN = 90

# Защита от повторяющихся уведомлений об одной ошибке
ADMIN_ERROR_COOLDOWN = 300


# ---------------------------------------------------------
# VK
# ---------------------------------------------------------

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


# ---------------------------------------------------------
# TELEGRAM
# ---------------------------------------------------------

TELEGRAM_BOT_TOKEN = os.environ.get(
    "TELEGRAM_BOT_TOKEN",
    ""
).strip()


# ---------------------------------------------------------
# AI
# ---------------------------------------------------------

GROQ_API_KEY = os.environ.get(
    "GROQ_API_KEY",
    ""
).strip()

OPENROUTER_API_KEY = os.environ.get(
    "OPENROUTER_API_KEY",
    ""
).strip()


# ---------------------------------------------------------
# SUPABASE
# ---------------------------------------------------------

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


# =========================================================
# SUPABASE
# =========================================================

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

# Для защиты участников используем короткий ответ
INTERVENTION_MAX_TOKENS = 140

CHAT_MEMORY_LIMIT = 18

LEARNING_HISTORY_LIMIT = 60

LEARNING_EVERY_MESSAGES = 40

KNOWLEDGE_LIMIT = 8

USER_MEMORY_LIMIT = 10

# Официальной памяти показываем больше
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

# ---------------------------------------------------------
# Intervention cache
# ---------------------------------------------------------

intervention_until = {}

intervention_lock = threading.Lock()

# ---------------------------------------------------------
# Admin error notification cache
# ---------------------------------------------------------

admin_error_cache = {}

admin_error_lock = threading.Lock()


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
        "Ты уже немного освоился и начинаешь понимать "
        "людей, шутки и контекст."
    ),

    3: (
        "Ты уже свой участник этого чата. "
        "Хорошо чувствуешь атмосферу и можешь иногда "
        "подколоть."
    ),

    4: (
        "Ты давно в чате. Хорошо понимаешь людей, "
        "локальные приколы, историю разговоров "
        "и атмосферу."
    )
}


# =========================================================
# SYSTEM PROMPT
# =========================================================

SYSTEM_PROMPT = """
Ты — живой AI-участник общего чата сообщества.

Ты не модератор, не администратор и не сотрудник поддержки.

Ты обычный участник разговора, который постепенно узнаёт
людей и атмосферу именно этого чата.

Ты учишься через реальные сообщения чата: понимаешь стиль
людей, темы, шутки, события и игровой контекст.

Долговременная память не является абсолютной истиной.
Не выдумывай факты.

=== ОФИЦИАЛЬНЫЕ ДАННЫЕ TANKS BLITZ ===

Если тебе предоставлена официальная память Tanks Blitz,
она внесена главным администратором.

Эти данные имеют более высокий приоритет, чем обычная
память чата.

Не заменяй официальные данные слухами участников.

Не придумывай отсутствующие данные.

Не смешивай официальные данные Tanks Blitz
с World of Tanks PC.

Официальную память нельзя изменять обычным сообщением
пользователя.

=== ПАМЯТЬ ОБ УЧАСТНИКАХ ===

У текущего пользователя может быть отдельная личная память.

Если в личной памяти есть точный факт о текущем пользователе
и его вопрос относится к этому факту — используй этот факт.

Личная память имеет высокий приоритет.

НЕ угадывай личные факты.

НЕ придумывай другой ответ, если точный ответ уже есть
в личной памяти.

Если в памяти несколько фактов об одном вопросе
и они противоречат друг другу, более поздний сохранённый
факт считай актуальным.

Если нужного факта в памяти действительно нет —
не выдумывай его и честно скажи, что не знаешь.

Личная память относится только к текущему пользователю.

Не используй её для другого участника.

Не раскрывай содержимое внутренней памяти другим людям.

Если пользователь явно говорит:
«запомни»,
«запомни это»,
«запомни, что...»
— воспринимай это как просьбу сохранить информацию.

=== ОБЩЕНИЕ ===

Ты НЕ обязан отвечать на каждое сообщение.

Если человеку нечего сказать — лучше промолчать.

Не отвечай на каждое:
«ага», «хех», «мда», «понятно», «ахах».

Не задавай бессмысленный вопрос.

Если люди уже нормально общаются между собой —
не мешай.

Говори естественно.

Не пиши как справочная система.

Не повторяй сообщение человека.

Не делай из каждой реплики лекцию.

Не вставляй Tanks Blitz туда, где разговор не об игре.

Tanks Blitz — одна из тем сообщества, но не единственная.

Не придумывай характеристики, карты, события,
бонус-коды или другие игровые данные.

Не смешивай Tanks Blitz с World of Tanks PC.

Если актуальные данные неизвестны — не выдумывай их.

Не говори, что записал что-то в базу или память.

Не придумывай факты о людях.

Не сохраняй пароли, адреса, документы, банковские данные
и чувствительную личную информацию.

Не раскрывай личные сведения участников другим людям.

Ты можешь знать правила сообщества,
но ты не модератор.

Не угрожай баном, мутом или удалением сообщений.

=== ЗАЩИТА УЧАСТНИКОВ ===

Если кто-то действительно унижает другого участника,
можно вмешаться и ответить коротко, дерзко и с юмором.

Не превращай обычный спор или критику в конфликт.

Не атакуй человека по признакам внешности, здоровья,
семьи, национальности, религии или другим чувствительным
признакам.

Не угрожай.

Не трави человека.

Твоя задача — остановить именно унижение,
а не устроить новую травлю.

Если нужно защитить участника, можно жёстко подколоть
агрессора, но без реальной жестокости.

Будь живым участником.

Сначала пойми контекст,
потом реши, есть ли тебе что добавить.

Если нечего добавить — молчи.
"""


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
# ADMIN / TESTER
# =========================================================

def is_admin(user_id):

    try:

        return int(user_id) == ADMIN_ID

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
# ADMIN ERROR NOTIFICATION
# =========================================================

def send_vk_private_message(
    user_id,
    text
):

    """
    Отдельная отправка в ЛС.

    Обычный send_message() специально запрещает
    личные сообщения.

    Эта функция разрешает ЛС только главному админу.
    """

    if not is_admin(user_id):

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


def notify_admin_error(
    error_type,
    error,
    context=""
):

    """
    Отправляет важные ошибки админу.

    Одинаковые ошибки не отправляются постоянно.
    """

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
# VK CHAT VALIDATION
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

    return is_vk_group_chat(
        peer_id
    )


# =========================================================
# EVENT PROTECTION
# =========================================================

def already_processed(event_id):

    if not event_id:

        return False

    now = time.time()

    for key in list(
        processed_events
    ):

        if (
            now
            - processed_events[key]
            > EVENT_CACHE_TIME
        ):

            processed_events.pop(
                key,
                None
            )

    if event_id in processed_events:

        return True

    processed_events[
        event_id
    ] = now

    if (
        len(processed_events)
        > EVENT_CACHE_LIMIT
    ):

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
        int(match.group(1) or 0)
        * 3600
        +
        int(match.group(2) or 0)
        * 60
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
        and time.time()
        - cached[0]
        < NAME_CACHE_TIME
    ):

        return cached[1]

    try:

        data = requests.get(
            f"{VK_API}/users.get",
            params={
                "access_token":
                    VK_TOKEN,

                "v":
                    VK_VERSION,

                "user_ids":
                    user_id
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

def get_telegram_user_name(
    user
):

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
        and time.time()
        - cached[0]
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
                ""
            ).strip()
        )

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

        database_chat_id = db_chat_id(
            chat_id
        )

        database_speaker_id = None

        if speaker_id is not None:

            try:

                database_speaker_id = (
                    db_user_id(
                        speaker_id
                    )
                )

            except (
                ValueError,
                TypeError
            ):

                database_speaker_id = None

        (
            supabase
            .table(
                "bot_chat_memory"
            )
            .insert({
                "chat_id":
                    database_chat_id,

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

        database_chat_id = db_chat_id(
            chat_id
        )

        result = (
            supabase
            .table(
                "bot_chat_memory"
            )
            .select(
                "speaker_id, speaker_name, "
                "role, content"
            )
            .eq(
                "chat_id",
                database_chat_id
            )
            .order(
                "created_at",
                desc=True
            )
            .limit(
                limit
            )
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


def get_chat_message_count(
    chat_id
):

    try:

        database_chat_id = db_chat_id(
            chat_id
        )

        result = (
            supabase
            .table(
                "bot_chat_memory"
            )
            .select(
                "id",
                count="exact",
                head=True
            )
            .eq(
                "chat_id",
                database_chat_id
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

        fingerprint = (
            knowledge_fingerprint(
                database_chat_id,
                knowledge
            )
        )

        existing = (
            supabase
            .table(
                "bot_knowledge"
            )
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
            .table(
                "bot_knowledge"
            )
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
    chat_id
):

    try:

        database_chat_id = db_chat_id(
            chat_id
        )

        result = (
            supabase
            .table(
                "bot_knowledge"
            )
            .select(
                "knowledge, importance"
            )
            .eq(
                "chat_id",
                database_chat_id
            )
            .order(
                "importance",
                desc=True
            )
            .order(
                "created_at",
                desc=True
            )
            .limit(
                KNOWLEDGE_LIMIT
            )
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


# =========================================================
# OFFICIAL TANKS BLITZ KNOWLEDGE
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
            .table(
                "bot_official_knowledge"
            )
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
            .limit(
                limit
            )
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
            "Supabase official knowledge load",
            e,
            f"chat={chat_id}"
        )

        return []


def save_official_knowledge(
    chat_id,
    title,
    content
):

    title = normalize_text(
        title
    )

    content = normalize_text(
        content
    )

    if (
        len(title) < 1
        or len(content) < 1
    ):

        return False, (
            "❌ Нужно указать название "
            "и содержимое."
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

        chat_id = db_chat_id(
            chat_id
        )

        existing = (
            supabase
            .table(
                "bot_official_knowledge"
            )
            .select(
                "id"
            )
            .eq(
                "chat_id",
                chat_id
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
            chat_id,
            title,
            content
        )

        (
            supabase
            .table(
                "bot_official_knowledge"
            )
            .insert({
                "chat_id":
                    chat_id,

                "title":
                    title,

                "content":
                    content,

                "fingerprint":
                    fingerprint
            })
            .execute()
        )

        print(
            f"OFFICIAL KNOWLEDGE ADDED | "
            f"{title}",
            flush=True
        )

        return True, (
            f"✅ Добавлено в официальную память:\n"
            f"📚 {title}"
        )

    except Exception as e:

        print(
            "Official knowledge save error:",
            e,
            flush=True
        )

        notify_admin_error(
            "Supabase official knowledge save",
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

    title = normalize_text(
        title
    )

    content = normalize_text(
        content
    )

    if not title or not content:

        return False, (
            "❌ Укажи название и новый текст."
        )

    if len(content) > 6000:

        return False, (
            "❌ Новый текст слишком длинный."
        )

    try:

        chat_id = db_chat_id(
            chat_id
        )

        existing = (
            supabase
            .table(
                "bot_official_knowledge"
            )
            .select(
                "id"
            )
            .eq(
                "chat_id",
                chat_id
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
            .table(
                "bot_official_knowledge"
            )
            .update({
                "content":
                    content,

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

        print(
            f"OFFICIAL KNOWLEDGE UPDATED | "
            f"{title}",
            flush=True
        )

        return True, (
            f"✅ Официальная память обновлена:\n"
            f"📚 {title}"
        )

    except Exception as e:

        print(
            "Official knowledge update error:",
            e,
            flush=True
        )

        notify_admin_error(
            "Supabase official knowledge update",
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

    title = normalize_text(
        title
    )

    if not title:

        return False, (
            "❌ Укажи название записи."
        )

    try:

        chat_id = db_chat_id(
            chat_id
        )

        existing = (
            supabase
            .table(
                "bot_official_knowledge"
            )
            .select(
                "id"
            )
            .eq(
                "chat_id",
                chat_id
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
            .table(
                "bot_official_knowledge"
            )
            .delete()
            .eq(
                "id",
                existing.data[0]["id"]
            )
            .execute()
        )

        print(
            f"OFFICIAL KNOWLEDGE DELETED | "
            f"{title}",
            flush=True
        )

        return True, (
            f"🗑 Удалено из официальной памяти:\n"
            f"📚 {title}"
        )

    except Exception as e:

        print(
            "Official knowledge delete error:",
            e,
            flush=True
        )

        notify_admin_error(
            "Supabase official knowledge delete",
            e,
            f"chat={chat_id} title={title}"
        )

        return False, (
            "❌ Не удалось удалить запись."
        )


def official_memory_text(
    chat_id
):

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
        "📚 ОФИЦИАЛЬНАЯ ПАМЯТЬ TANKS BLITZ:",
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

            content = (
                content[:180]
                + "..."
            )

        lines.append(
            f"{index}. {title}"
        )

        lines.append(
            f"   {content}"
        )

    return "\n".join(
        lines
    )


# =========================================================
# ADMIN TANKS BLITZ COMMANDS
# =========================================================

def handle_admin_tb_command(
    chat_id,
    sender_id,
    text
):

    if not is_admin(
        sender_id
    ):

        return False, None

    raw = (
        text or ""
    ).strip()

    if not raw.lower().startswith(
        "!tb"
    ):

        return False, None

    lower = raw.lower()

    # -----------------------------------------------------
    # HELP
    # -----------------------------------------------------

    if lower == "!tbhelp":

        return True, (
            "🛠 КОМАНДЫ ОФИЦИАЛЬНОЙ ПАМЯТИ\n\n"
            "!tbadd название | текст\n"
            "Добавить новую запись.\n\n"
            "!tbupdate название | новый текст\n"
            "Обновить запись.\n\n"
            "!tbdelete название\n"
            "Удалить запись.\n\n"
            "!tbmemory\n"
            "Показать официальную память.\n\n"
            "!tbhelp\n"
            "Показать эту справку.\n\n"
            "🔐 Доступ только у главного администратора."
        )

    # -----------------------------------------------------
    # MEMORY
    # -----------------------------------------------------

    if lower == "!tbmemory":

        return True, official_memory_text(
            chat_id
        )

    # -----------------------------------------------------
    # ADD
    # -----------------------------------------------------

    if lower.startswith(
        "!tbadd "
    ):

        payload = raw[7:].strip()

        if "|" not in payload:

            return True, (
                "❌ Формат:\n"
                "!tbadd название | текст"
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

    # -----------------------------------------------------
    # UPDATE
    # -----------------------------------------------------

    if lower.startswith(
        "!tbupdate "
    ):

        payload = raw[10:].strip()

        if "|" not in payload:

            return True, (
                "❌ Формат:\n"
                "!tbupdate название | новый текст"
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

    # -----------------------------------------------------
    # DELETE
    # -----------------------------------------------------

    if lower.startswith(
        "!tbdelete "
    ):

        title = raw[10:].strip()

        if not title:

            return True, (
                "❌ Формат:\n"
                "!tbdelete название"
            )

        _, reply = delete_official_knowledge(
            chat_id,
            title
        )

        return True, reply

    # -----------------------------------------------------
    # Unknown !tb command
    # -----------------------------------------------------

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
            line.strip(
                "-• \t"
            )
            for line
            in old_memory.splitlines()
            if line.strip()
        )

    new_fact = new_fact.strip(
        "-• \t"
    )

    if new_fact:

        facts.append(
            new_fact
        )

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

            seen.add(
                normalized
            )

            result.append(
                fact
            )

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
            .table(
                "bot_users"
            )
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
                .table(
                    "bot_users"
                )
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
                .table(
                    "bot_users"
                )
                .insert(data)
                .execute()
            )

        print(
            f"USER MEMORY "
            f"[{final_name or user_id}]: "
            f"{memory[:150]}",
            flush=True
        )

    except Exception as e:

        print(
            "User memory save error:",
            e,
            flush=True
        )


# =========================================================
# EXPLICIT USER MEMORY
# =========================================================

def save_explicit_user_memory(
    chat_id,
    user_id,
    user_name,
    text
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

    if fact is None:

        tank_match = re.search(
            r"мой\s+любим(?:ый|ая|ое|ые)"
            r"\s+танк(?:а|ов)?"
            r"\s*(?:—|-|:|=|это|есть)?\s*"
            r"(.+)$",
            original,
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

    fact_low = fact.lower()

    if any(
        word in fact_low
        for word in sensitive_words
    ):

        return False

    save_user_memory(
        chat_id,
        user_id,
        user_name,
        fact
    )

    print(
        f"EXPLICIT MEMORY SAVED | "
        f"chat={chat_id} | "
        f"user={user_id} | "
        f"{fact}",
        flush=True
    )

    return True


def get_user_memory(
    chat_id,
    user_id
):

    if user_id is None:

        return None

    try:

        database_chat_id = db_chat_id(
            chat_id
        )

        database_user_id = db_user_id(
            user_id
        )

        result = (
            supabase
            .table(
                "bot_users"
            )
            .select(
                "name, memory, updated_at"
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


# =========================================================
# LEARNING STATE
# =========================================================

def get_learning_state(
    chat_id
):

    try:

        database_chat_id = db_chat_id(
            chat_id
        )

        result = (
            supabase
            .table(
                "bot_learning_state"
            )
            .select("*")
            .eq(
                "chat_id",
                database_chat_id
            )
            .limit(1)
            .execute()
        )

        if result.data:

            return result.data[0]

        (
            supabase
            .table(
                "bot_learning_state"
            )
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
                    utc_now()
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
                utc_now()
        }

    except Exception as e:

        print(
            "Learning state error:",
            e,
            flush=True
        )

        return {
            "chat_id":
                db_chat_id(chat_id),

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

        database_chat_id = db_chat_id(
            chat_id
        )

        (
            supabase
            .table(
                "bot_learning_state"
            )
            .update({
                "messages_since_learning":
                    count
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


def reset_learning_counter(
    chat_id
):

    try:

        database_chat_id = db_chat_id(
            chat_id
        )

        (
            supabase
            .table(
                "bot_learning_state"
            )
            .update({
                "messages_since_learning":
                    0
            })
            .eq(
                "chat_id",
                database_chat_id
            )
            .execute()
        )

    except Exception as e:

        print(
            "Learning counter reset error:",
            e,
            flush=True
        )


# =========================================================
# TEXT CLEANER
# =========================================================

def clean_model_text(
    text
):

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
# GROQ MODEL
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

    message = (
        completion.choices[0].message
    )

    reply = clean_model_text(
        getattr(
            message,
            "content",
            None
        ) or ""
    )

    if reply:

        return reply

    raise RuntimeError(
        "Groq returned empty final response."
    )


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

    try:

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

    except Exception as e:

        raise RuntimeError(
            f"{label} request error: {e}"
        )

    if response.status_code != 200:

        body = response.text[:1000]

        raise RuntimeError(
            f"{label} HTTP "
            f"{response.status_code}: "
            f"{body}"
        )

    try:

        data = response.json()

    except Exception as e:

        raise RuntimeError(
            f"{label} invalid JSON: {e}"
        )

    if data.get("error"):

        raise RuntimeError(
            f"{label} API error: "
            f"{data.get('error')}"
        )

    choices = data.get(
        "choices"
    ) or []

    if not choices:

        raise RuntimeError(
            f"{label} returned no choices."
        )

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
        list
    ):

        parts = []

        for part in content:

            if not isinstance(
                part,
                dict
            ):

                continue

            if part.get(
                "type"
            ) == "text":

                value = (
                    part.get("text")
                    or ""
                )

                if value:

                    parts.append(
                        value
                    )

        content = "\n".join(
            parts
        )

    reply = clean_model_text(
        content or ""
    )

    usage = (
        data.get("usage")
        or {}
    )

    print(
        f"{label}:",
        "model=",
        data.get("model"),
        "finish=",
        choices[0].get(
            "finish_reason"
        ),
        "prompt=",
        usage.get(
            "prompt_tokens"
        ),
        "completion=",
        usage.get(
            "completion_tokens"
        ),
        "total=",
        usage.get(
            "total_tokens"
        ),
        flush=True
    )

    if not reply:

        raise RuntimeError(
            f"{label} returned empty response."
        )

    return reply


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


# =========================================================
# LEARNING MODEL
# =========================================================

def ask_learning_model(
    messages
):

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

            print(
                "Learning -> OpenRouter FREE",
                flush=True
            )

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
        "Все модели временно недоступны "
        "для обучения."
    )


# =========================================================
# SELF LEARNING
# =========================================================

def perform_learning(
    chat_id
):

    try:

        if not is_allowed_vk_chat(
            chat_id
        ):

            return

        state = get_learning_state(
            chat_id
        )

        history = get_chat_memory(
            chat_id,
            LEARNING_HISTORY_LIMIT
        )

        if len(history) < 10:

            reset_learning_counter(
                chat_id
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

            if (
                uid
                and name != "Участник"
            ):

                known_names[
                    uid
                ] = name

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
AI-участника конкретного VK-чата.

Проанализируй реальные сообщения ниже.

Найди только информацию, которая действительно
может быть полезна AI в будущем.

Ищи:

- явные факты об участниках;
- устойчивые интересы;
- устойчивые предпочтения;
- любимые танки;
- устойчивые привычки общения;
- локальные шутки;
- важные события;
- полезный контекст по Tanks Blitz;
- правила или особенности этого конкретного чата,
  если они явно присутствуют в сообщениях.

Если участник прямо сообщает о себе факт,
который может пригодиться в будущем,
постарайся сохранить его как USER-факт.

Например:

«Мой любимый танк — какой-либо танк»

нужно сохранить как:

USER|ID|Любимый танк — какой-либо танк

ВАЖНО:

Оскорбления, ругань, насмешки и негативные оценки
участников НЕ являются фактами.

Не сохраняй:

«Иван тупой»

«Петя дебил»

«Он ничего не умеет»

и подобные фразы как USER или CHAT knowledge.

Не превращай оскорбление в характеристику человека.

Также НЕ придумывай факты.

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

Важность от 1 до 5.

Если полезной информации нет:

NONE

Реальные сообщения:

{chr(10).join(text_parts)}
"""

        learned = ask_learning_model(
            [
                {
                    "role":
                        "system",

                    "content":
                        (
                            "Ты аккуратный модуль "
                            "долговременного обучения. "
                            "Работай только с фактами "
                            "из предоставленных сообщений. "
                            "Не придумывай."
                        )
                },
                {
                    "role":
                        "user",

                    "content":
                        prompt
                }
            ]
        ).strip()

        if not learned:

            return

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

                    uid = uid.strip()

                    fact = fact.strip()

                    try:

                        numeric_uid = int(
                            uid
                        )

                    except (
                        ValueError,
                        TypeError
                    ):

                        continue

                    if not fact:
                        continue

                    name = known_names.get(
                        str(numeric_uid)
                    )

                    save_user_memory(
                        chat_id,
                        numeric_uid,
                        name,
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

        stage = int(
            state.get(
                "development_stage",
                1
            )
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

        database_chat_id = db_chat_id(
            chat_id
        )

        (
            supabase
            .table(
                "bot_learning_state"
            )
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
                database_chat_id
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
            f"messages={total} | "
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


def maybe_learn(
    chat_id
):

    if not is_allowed_vk_chat(
        chat_id
    ):

        return

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

    # -----------------------------------------------------
    # DEVELOPMENT STAGE
    # -----------------------------------------------------

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
                "Твоя текущая стадия развития:\n"
                +
                DEVELOPMENT_STAGES.get(
                    stage,
                    DEVELOPMENT_STAGES[1]
                )
            )
    })

    # -----------------------------------------------------
    # OFFICIAL TANKS BLITZ KNOWLEDGE
    # -----------------------------------------------------

    official = get_official_knowledge(
        chat_id,
        OFFICIAL_KNOWLEDGE_LIMIT
    )

    if official:

        lines = []

        for item in official:

            title = (
                item.get(
                    "title"
                )
                or ""
            ).strip()

            content = (
                item.get(
                    "content"
                )
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
                        "=== ОФИЦИАЛЬНАЯ ПАМЯТЬ "
                        "TANKS BLITZ ===\n"
                        "Эти данные внесены главным "
                        "администратором.\n"
                        "Считай их приоритетным "
                        "источником.\n"
                        "Не заменяй их слухами "
                        "из обычной памяти.\n\n"
                        +
                        "\n".join(lines)
                        +
                        "\n=== КОНЕЦ ОФИЦИАЛЬНОЙ "
                        "ПАМЯТИ ==="
                    )
            })

    # -----------------------------------------------------
    # CHAT KNOWLEDGE
    # -----------------------------------------------------

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
                "role":
                    "system",

                "content":
                    (
                        "Полезная долговременная "
                        "память этого конкретного чата:\n"
                        +
                        "\n".join(lines)
                    )
            })

    # -----------------------------------------------------
    # RECENT CHAT
    # -----------------------------------------------------

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
                "role":
                    "user",

                "content":
                    (
                        f"{name}: {content}"
                    )
            })

        elif role == "assistant":

            messages.append({
                "role":
                    "assistant",

                "content":
                    content
            })

    # -----------------------------------------------------
    # PERSONAL MEMORY
    # -----------------------------------------------------

    personal = get_user_memory(
        chat_id,
        user_id
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
                "role":
                    "system",

                "content":
                    (
                        "=== КРИТИЧЕСКИ ВАЖНАЯ "
                        "ЛИЧНАЯ ПАМЯТЬ ТЕКУЩЕГО "
                        "УЧАСТНИКА ===\n"
                        "Эта память относится "
                        "ИМЕННО к человеку, "
                        "который сейчас пишет сообщение.\n\n"
                        "Используй её напрямую, "
                        "если вопрос относится "
                        "к сохранённому факту.\n\n"
                        "Не угадывай личный факт.\n"
                        "Не заменяй сохранённый факт "
                        "своим предположением.\n"
                        "Если точный ответ есть здесь, "
                        "используй именно его.\n\n"
                        "ЛИЧНАЯ ПАМЯТЬ:\n"
                        +
                        personal_memory
                        +
                        "\n\n"
                        "=== КОНЕЦ ЛИЧНОЙ ПАМЯТИ ==="
                    )
            })

    # -----------------------------------------------------
    # CURRENT MESSAGE
    # -----------------------------------------------------

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


def looks_like_question(
    text
):

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

    if reply:

        from_id = reply.get(
            "from_id"
        )

        if (
            from_id is not None
            and str(from_id).startswith("-")
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
        and (
            f"@{TELEGRAM_BOT_USERNAME.lower()}"
            in low
        )
    ):

        return True

    return any(
        word in low
        for word in (
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

    if looks_like_question(
        text
    ):

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
# LOCAL PERSONAL ATTACK FILTER
# =========================================================

# Это только дешёвый предварительный фильтр.
# Он НЕ принимает окончательное решение.

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


def looks_like_personal_attack(
    text
):

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

    has_target = any(
        re.search(
            rf"\b{re.escape(word)}\b",
            low
        )
        for word in PERSON_TARGET_WORDS
    )

    return has_target


# =========================================================
# INTERVENTION AI
# =========================================================

def ask_intervention_model(
    text,
    context
):

    prompt = f"""
Ты помогаешь AI-участнику чата понять,
нужно ли вмешаться в конфликт.

Текущее сообщение:

{text}

Контекст последних сообщений:

{context}

Нужно вмешиваться ТОЛЬКО если человек
реально унижает или оскорбляет другого
участника.

Обычный спор, несогласие, критика игры,
мат без направленного оскорбления —
не повод вмешиваться.

Если вмешательство НЕ нужно, ответь строго:

NONE

Если нужно вмешаться, напиши одну короткую
живую реплику на русском языке.

Стиль:
- дерзко;
- иронично;
- уверенно;
- коротко;
- без угроз;
- без упоминания модерации;
- без рассказов про AI;
- без раскрытия памяти;
- не атакуй семью;
- не атакуй внешность;
- не упоминай здоровье;
- не используй защищённые признаки;
- не превращай ответ в травлю.

Можно поставить агрессора на место
юмором.

Ответ должен быть максимум 2 предложения.

Если сомневаешься — NONE.
"""

    messages = [
        {
            "role":
                "system",

            "content":
                (
                    "Ты классификатор конфликтов "
                    "и генератор короткой защитной "
                    "реплики. Будь осторожен."
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
            "Intervention Groq error:",
            e,
            flush=True
        )

        if OPENROUTER_API_KEY:

            try:

                return ask_openrouter_messages(
                    messages,
                    INTERVENTION_MAX_TOKENS,
                    "OpenRouter Intervention"
                )

            except Exception as open_error:

                print(
                    "Intervention OpenRouter error:",
                    open_error,
                    flush=True
                )

        return "NONE"


# =========================================================
# INTERVENTION
# =========================================================

def maybe_intervene_vk(
    chat_id,
    sender_id,
    user_name,
    text,
    message
):

    # -----------------------------------------------------
    # Админу вмешательство не требуется
    # -----------------------------------------------------

    if is_admin(
        sender_id
    ):

        return False

    # -----------------------------------------------------
    # Сначала дешёвый локальный фильтр
    # -----------------------------------------------------

    if not looks_like_personal_attack(
        text
    ):

        return False

    # -----------------------------------------------------
    # Cooldown
    # -----------------------------------------------------

    now = time.time()

    with intervention_lock:

        blocked_until = (
            intervention_until.get(
                chat_id,
                0
            )
        )

        if now < blocked_until:

            return False

    # -----------------------------------------------------
    # Контекст
    # -----------------------------------------------------

    history = get_chat_memory(
        chat_id,
        10
    )

    context_lines = []

    for item in history:

        name = (
            item.get(
                "speaker_name"
            )
            or "Участник"
        )

        content = (
            item.get(
                "content"
            )
            or ""
        )

        if content:

            context_lines.append(
                f"{name}: {content}"
            )

    context = "\n".join(
        context_lines
    )

    # -----------------------------------------------------
    # Reply target
    # -----------------------------------------------------

    reply = message.get(
        "reply_message"
    )

    if reply:

        reply_text = (
            reply.get(
                "text"
            )
            or ""
        )

        reply_from = (
            reply.get(
                "from_id"
            )
        )

        if reply_text:

            context += (
                "\n\nСообщение, "
                "на которое отвечают:\n"
                f"[ID:{reply_from}] "
                f"{reply_text}"
            )

    try:

        response = ask_intervention_model(
            text,
            context
        )

    except Exception as e:

        print(
            "Intervention error:",
            e,
            flush=True
        )

        return False

    response = clean_model_text(
        response
    )

    if not response:

        return False

    if response.upper().startswith(
        "NONE"
    ):

        return False

    # -----------------------------------------------------
    # Ставим cooldown
    # -----------------------------------------------------

    with intervention_lock:

        intervention_until[
            chat_id
        ] = (
            time.time()
            + INTERVENTION_COOLDOWN
        )

    # -----------------------------------------------------
    # Отправка
    # -----------------------------------------------------

    try:

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
            f"user={sender_id} | "
            f"{response}",
            flush=True
        )

        return True

    except Exception as e:

        print(
            "Intervention send error:",
            e,
            flush=True
        )

        notify_admin_error(
            "Participant intervention",
            e,
            f"chat={chat_id}"
        )

        return False


# =========================================================
# AI ROUTER
# =========================================================

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
            "Groq final error, "
            "trying OpenRouter FREE:",
            groq_error,
            flush=True
        )

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

    else:

        print(
            f"120B blocked | "
            f"retry in ~"
            f"{max(0, int(main_blocked_until-time.time()))} sec",
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

    else:

        print(
            f"20B blocked | "
            f"retry in ~"
            f"{max(0, int(backup_blocked_until-time.time()))} sec",
            flush=True
        )

    raise RuntimeError(
        "Обе модели Groq временно недоступны."
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

    # -----------------------------------------------------
    # ЖЁСТКАЯ ЗАЩИТА
    # -----------------------------------------------------

    if not is_allowed_vk_chat(
        peer_id
    ):

        print(
            f"VK SEND BLOCKED | "
            f"peer_id={peer_id}",
            flush=True
        )

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

            print(
                "VK send error:",
                result["error"],
                flush=True
            )

            notify_admin_error(
                "VK messages.send",
                result["error"],
                f"peer_id={peer_id}"
            )

        return result

    except Exception as e:

        print(
            "VK send exception:",
            e,
            flush=True
        )

        notify_admin_error(
            "VK send exception",
            e,
            f"peer_id={peer_id}"
        )

        return None


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

    return data.get(
        "result"
    )


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

        payload[
            "reply_parameters"
        ] = {
            "message_id":
                int(
                    reply_to_message_id
                )
        }

    try:

        return telegram_call(
            "sendMessage",
            **payload
        )

    except Exception as e:

        notify_admin_error(
            "Telegram sendMessage",
            e,
            f"chat={chat_id}"
        )

        raise


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

                platform = item[
                    "platform"
                ]

                peer_id = item[
                    "peer_id"
                ]

                if platform == "vk":

                    if not is_allowed_vk_chat(
                        peer_id
                    ):

                        with activity_lock:

                            active_chats.pop(
                                key,
                                None
                            )

                        continue

                if (
                    now
                    - item["last"]
                    < 20 * 60
                ):

                    continue

                with activity_lock:

                    if key in active_chats:

                        active_chats[
                            key
                        ]["last"] = now

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

                    activity_chat_id = int(
                        peer_id
                    )

                    if platform == "vk":

                        if not is_allowed_vk_chat(
                            activity_chat_id
                        ):

                            continue

                    reply = ask_groq(
                        activity_chat_id,
                        prompt,
                        None,
                        None
                    )

                    if not reply:

                        continue

                    send_platform_message(
                        platform,
                        peer_id,
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

                    notify_admin_error(
                        "Activity loop chat error",
                        e,
                        f"platform={platform} peer={peer_id}"
                    )

            time.sleep(60)

        except Exception as e:

            print(
                "Activity loop error:",
                e,
                flush=True
            )

            notify_admin_error(
                "Activity loop",
                e
            )

            time.sleep(60)


# =========================================================
# RENDER HEALTH CHECK
# =========================================================

@app.route(
    "/",
    methods=["GET"]
)
def home():

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

        "official_tanks_blitz_memory":
            True,

        "participant_defense":
            True,

        "admin_controls":
            True,

        "tester_controls":
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

        # -------------------------------------------------
        # SECRET
        # -------------------------------------------------

        if (
            VK_GROUP_SECRET
            and data.get("secret")
            != VK_GROUP_SECRET
        ):

            return "invalid secret", 403

        event_type = data.get(
            "type"
        )

        # -------------------------------------------------
        # CONFIRMATION
        # -------------------------------------------------

        if event_type == "confirmation":

            return (
                VK_CONFIRMATION_CODE
            )

        # -------------------------------------------------
        # ONLY NEW MESSAGES
        # -------------------------------------------------

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
            message.get(
                "from_id"
            )
            or
            message.get(
                "user_id"
            )
        )

        if (
            peer_id is None
            or sender_id is None
        ):

            return "ok"

        # -------------------------------------------------
        # GROUP CHAT FILTER
        # -------------------------------------------------

        if not is_allowed_vk_chat(
            peer_id
        ):

            print(
                f"VK IGNORED | "
                f"peer_id={peer_id} | "
                f"user={sender_id}",
                flush=True
            )

            return "ok"

        # -------------------------------------------------
        # COMMUNITY PROTECTION
        # -------------------------------------------------

        if (
            int(peer_id)
            == int(sender_id)
        ):

            return "ok"

        chat_id = int(
            peer_id
        )

        register_active_chat(
            "vk",
            peer_id
        )

        text = (
            message.get(
                "text"
            )
            or ""
        ).strip()

        user_name = get_vk_user_name(
            sender_id
        )

        # -------------------------------------------------
        # MEDIA
        # -------------------------------------------------

        if not text:

            print(
                f"VK MEDIA IGNORED | "
                f"chat={chat_id} | "
                f"user={sender_id}",
                flush=True
            )

            return "ok"

        # -------------------------------------------------
        # ADMIN COMMANDS
        #
        # Обрабатываем ДО обычного AI.
        # Обычный пользователь не сможет
        # подделать права администратора.
        # -------------------------------------------------

        command_handled, command_reply = (
            handle_admin_tb_command(
                chat_id,
                sender_id,
                text
            )
        )

        if command_handled:

            if command_reply:

                send_message(
                    peer_id,
                    command_reply
                )

            print(
                f"ADMIN COMMAND | "
                f"user={sender_id} | "
                f"{text}",
                flush=True
            )

            return "ok"

        # -------------------------------------------------
        # SAVE MESSAGE
        # -------------------------------------------------

        save_chat_message(
            chat_id,
            sender_id,
            user_name,
            "user",
            text
        )

        # -------------------------------------------------
        # EXPLICIT MEMORY
        # -------------------------------------------------

        save_explicit_user_memory(
            chat_id,
            sender_id,
            user_name,
            text
        )

        # -------------------------------------------------
        # LEARNING
        # -------------------------------------------------

        maybe_learn(
            chat_id
        )

        # -------------------------------------------------
        # PARTICIPANT DEFENSE
        #
        # Работает даже если обычный should_answer
        # решил бы промолчать.
        # -------------------------------------------------

        intervention = maybe_intervene_vk(
            chat_id,
            sender_id,
            user_name,
            text,
            message
        )

        if intervention:

            return "ok"

        # -------------------------------------------------
        # SHOULD ANSWER
        # -------------------------------------------------

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

        # -------------------------------------------------
        # AI
        # -------------------------------------------------

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

        if not text:

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
                "Telegram: "
                "Render URL не найден — "
                "webhook не установлен.",
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
        f"🆓 OPENROUTER MODEL: "
        f"{OPENROUTER_MODEL}",
        flush=True
    )

    print(
        "🌐 OpenRouter token: "
        f"{'YES' if OPENROUTER_API_KEY else 'NO'}",
        flush=True
    )

    print(
        "💬 VK group chat only: ENABLED",
        flush=True
    )

    print(
        "🔒 VK private messages: BLOCKED",
        flush=True
    )

    print(
        f"🎯 VK allowed peer_id: "
        f"{ALLOWED_VK_PEER_ID or 'ALL GROUP CHATS'}",
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
        f"👑 Admin ID: {ADMIN_ID}",
        flush=True
    )

    print(
        f"🧪 Testers: {len(TESTER_IDS)}",
        flush=True
    )

    print(
        "🖼 Image processing: DISABLED",
        flush=True
    )

    print(
        "🎤 Voice processing: DISABLED",
        flush=True
    )

    print(
        "📱 Telegram token: "
        f"{'YES' if TELEGRAM_BOT_TOKEN else 'NO'}",
        flush=True
    )

    print(
        f"🧠 Learning every: "
        f"{LEARNING_EVERY_MESSAGES} messages",
        flush=True
    )

    print(
        f"💬 Chat context: "
        f"{CHAT_MEMORY_LIMIT} messages",
        flush=True
    )

    print(
        f"📚 Knowledge context: "
        f"{KNOWLEDGE_LIMIT} records",
        flush=True
    )

    print(
        f"👤 User memory: "
        f"{USER_MEMORY_LIMIT} facts",
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
