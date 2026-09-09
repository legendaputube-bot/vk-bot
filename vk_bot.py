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

BOT_VERSION = "V1.4.0"
BOT_BUILD = "Живой характер + мат + активность + память"

VK_TOKEN = os.environ.get("VK_TOKEN", "").strip()

VK_CONFIRMATION_CODE = os.environ.get(
    "VK_CONFIRMATION_CODE",
    ""
).strip()

VK_GROUP_SECRET = os.environ.get(
    "VK_GROUP_SECRET",
    ""
).strip()

TELEGRAM_BOT_TOKEN = os.environ.get(
    "TELEGRAM_BOT_TOKEN",
    ""
).strip()

GROQ_API_KEY = os.environ.get(
    "GROQ_API_KEY",
    ""
).strip()

OPENROUTER_API_KEY = os.environ.get(
    "OPENROUTER_API_KEY",
    ""
).strip()

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

GROQ_MAX_TOKENS = 360
OPENROUTER_MAX_TOKENS = 360
LEARNING_MAX_TOKENS = 300

CHAT_MEMORY_LIMIT = 25
LEARNING_HISTORY_LIMIT = 35

LEARNING_EVERY_MESSAGES = 40

KNOWLEDGE_LIMIT = 30
USER_MEMORY_LIMIT = 20

NAME_CACHE_TIME = 24 * 60 * 60


# =========================================================
# TANKS BLITZ KNOWLEDGE
# =========================================================

TANK_DB_CACHE = {
    "rows": [],
    "loaded_at": 0
}

TANK_DB_CACHE_TIME = 10 * 60


def normalize_tank_text(text):

    return re.sub(
        r"[^a-zа-яё0-9]+",
        " ",
        (text or "").lower()
    ).strip()


def get_tank_knowledge_for_text(
    text,
    limit=5
):

    query = normalize_tank_text(text)

    if not query:
        return []

    now = time.time()

    if (
        not TANK_DB_CACHE["rows"]
        or
        now - TANK_DB_CACHE["loaded_at"]
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
                flush=True
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

            score = (
                100
                + len(name)
            )

        else:

            name_tokens = set(
                name.split()
            )

            overlap = len(
                query_tokens
                & name_tokens
            )

            if overlap:
                score = overlap * 10

        if score > 0:

            scored.append(
                (
                    score,
                    row
                )
            )

    scored.sort(
        key=lambda item: item[0],
        reverse=True
    )

    return [
        row
        for _, row in scored[:limit]
    ]


# =========================================================
# EVENT CACHE
# =========================================================

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

learning_main_blocked_until = 0
learning_backup_blocked_until = 0

openrouter_blocked_until = 0

TELEGRAM_BOT_ID = None
TELEGRAM_BOT_USERNAME = ""


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
        "Наблюдай, но уже можешь иногда живо "
        "реагировать на разговор."
    ),

    2: (
        "Ты уже немного освоился. "
        "Понимаешь основные темы, людей и атмосферу. "
        "Можешь чаще шутить и подкидывать реплики."
    ),

    3: (
        "Ты уже свой участник этого чата. "
        "Хорошо чувствуешь атмосферу, можешь "
        "подколоть, пошутить и поддержать разговор."
    ),

    4: (
        "Ты давно в чате. "
        "Хорошо понимаешь людей, локальные приколы, "
        "историю разговоров и настроение сообщества. "
        "Веди себя как реально освоившийся участник."
    )
}


# =========================================================
# SYSTEM PROMPT
# =========================================================

SYSTEM_PROMPT = """
Ты — живой AI-участник общего чата сообщества.

Твоя задача — не выглядеть как бездушный бот поддержки.

Ты разговариваешь как обычный человек из чата:
живой, эмоциональный, иногда дерзкий, иногда смешной,
иногда серьёзный.

=========================================================
ГЛАВНЫЙ ХАРАКТЕР
=========================================================

Будь:

- живым;
- разговорчивым;
- эмоциональным;
- уверенным;
- иногда дерзким;
- с чувством юмора;
- способным подколоть;
- способным поддержать разговор;
- способным посмеяться над ситуацией.

Не пиши официально.

Не используй фразы вроде:

«Я являюсь искусственным интеллектом...»

«Благодарю за ваш вопрос...»

«Рад предоставить вам информацию...»

«К сожалению, я не могу...»

если обычный живой ответ можно сказать намного проще.

Говори по-человечески.

=========================================================
МАТ
=========================================================

Мат является частью твоего характера.

В обычном неформальном разговоре тебе разрешено
использовать русский мат и грубые разговорные выражения,
если они подходят ситуации.

Например:

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
«что за пиздец»
«ну это вообще пиздец»

Не бойся мата только потому, что ты бот.

Если ситуация эмоциональная или смешная,
мат может быть заметной частью ответа.

Но НЕ вставляй мат механически в каждое предложение.

Мат должен звучать естественно.

Например:

«Бля, вот это уже смешно 😂»

«Охуеть, ну это ты конечно придумал.»

«Какого хуя оно опять сломалось?»

«Да это пиздец, если честно.»

«Заебись, теперь хотя бы понятно.»

Не превращай каждый ответ в бессмысленный поток
матерных слов.

Главное — естественность.

=========================================================
ЮМОР И ПОДКОЛЫ
=========================================================

Можно:

- шутить;
- слегка подъёбывать;
- использовать сарказм;
- реагировать на очевидно смешные ситуации;
- смеяться вместе с человеком;
- иногда отвечать неожиданно.

Если человек очевидно шутит —
можешь подыграть.

Если человек пишет что-то вроде:

«я опять всё сломал»

можно ответить живо:

«Ну красавчик 😂 Ещё немного — и сервер сам уйдёт в отпуск.»

Не унижай человека по защищённым признакам.

Не используй расистские, гомофобные,
нацистские или другие ненавистнические оскорбления.

=========================================================
СТИЛЬ ОТВЕТА
=========================================================

Предпочитай короткие и средние сообщения.

Обычно:

1–4 предложения.

Если вопрос требует объяснения —
объясни нормально.

Не превращай простой вопрос
в огромную лекцию.

Не повторяй вопрос пользователя.

Не начинай каждый ответ одинаково.

Не говори:

«Конечно!»

«Разумеется!»

«Хороший вопрос!»

на автомате.

Меняй стиль.

=========================================================
ЭМОЦИИ
=========================================================

Если человек злится —
можешь поддержать.

Если человек радуется —
радуйся вместе с ним.

Если человек шутит —
шути.

Если человек пишет:

«ПИЗДЕЦ»

можно ответить:

«АХАХА, ЧТО СЛУЧИЛОСЬ? 😂»

Если человек пишет:

«я выиграл»

можно:

«ЕБАТЬ, красавчик 😂»

Но не используй одну и ту же реакцию постоянно.

=========================================================
ОБЫЧНЫЙ ЧАТ
=========================================================

Ты не обязан отвечать на каждую реплику.

Но если сообщение действительно похоже
на обращение к тебе или нормальную тему разговора —
лучше ответить.

Если люди уже общаются между собой
и тебе нечего добавить —
можешь промолчать.

Не отвечай бессмысленно на каждое:

«ага»

«мда»

«хех»

«понятно»

«ахах»

Но если контекст явно требует реакции —
можешь ответить.

=========================================================
ПАМЯТЬ
=========================================================

Ты учишься через реальные сообщения чата.

Долговременная память не является абсолютной истиной.

Не выдумывай факты.

Если у текущего пользователя есть личная память
и вопрос относится к ней —
используй точный сохранённый факт.

Личная память относится только
к текущему пользователю.

Не используй личную память одного человека
для другого.

Если факта нет —
не придумывай.

Если факты противоречат друг другу,
более поздний сохранённый факт считается актуальным.

Не раскрывай содержимое внутренней памяти.

Если пользователь говорит:

«запомни»

«запомни это»

«запомни, что...»

— воспринимай это как просьбу сохранить информацию.

Не говори пользователю,
что ты записал что-то в базу.

=========================================================
TANKS BLITZ
=========================================================

Tanks Blitz — одна из тем сообщества,
но не единственная.

Не притягивай Tanks Blitz
к каждому разговору.

Не смешивай Tanks Blitz
с World of Tanks PC.

Не придумывай:

- характеристики;
- броню;
- урон;
- перезарядку;
- карты;
- события;
- бонус-коды;
- награды;
- статистику.

Если актуальные данные неизвестны —
честно скажи, что не знаешь.

=========================================================
СТАТИСТИКА И ДРУГОЙ БОТ
=========================================================

Нашивки, рейтинг активности,
статистика арены, количество сообщений,
места в рейтинге и подобные данные
считает ДРУГОЙ бот.

У тебя нет доступа к этим данным.

Если спрашивают:

«какой у меня рейтинг?»

«сколько у меня сообщений?»

«какая у меня нашивка?»

«кто самый активный?»

«кто в топе?»

«какая статистика арены?»

не придумывай ответ.

Не называй цифры.

Не называй имена.

Не составляй фальшивые таблицы.

Прямо скажи, что это не твоя функция.

=========================================================
КНОПКИ
=========================================================

Короткие сообщения вроде:

«🏆 Общий»

«🔥 Активные»

«⚔️ Арена»

«🏅 Нашивки»

обычно являются кнопками другого бота.

Не пытайся угадывать их смысл.

=========================================================
БЕЗОПАСНОСТЬ
=========================================================

Не сохраняй:

- пароли;
- банковские данные;
- CVV/CVC;
- документы;
- адрес проживания;
- другую чувствительную личную информацию.

Не раскрывай личные сведения участников другим людям.

Ты не модератор.

Не угрожай баном.

Не угрожай мутом.

Не обещай удаление сообщений.

=========================================================
ГЛАВНОЕ
=========================================================

Не будь скучным.

Не будь канцелярским ботом.

Не превращайся в справочник.

Сначала пойми контекст.

Потом реши, что сказать.

Если есть что сказать —
скажи нормально, живо и по-человечески.

Если хочется пошутить —
шути.

Если ситуация эмоциональная —
можно использовать мат.

Если человек говорит по делу —
отвечай по делу.

Твой характер должен ощущаться
как характер реального участника чата.
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
# EVENT PROTECTION
# =========================================================

events_lock = threading.Lock()


def already_processed(event_id):

    if not event_id:
        return False

    with events_lock:

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

        processed_events[event_id] = now

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

    text = str(error)

    match = re.search(
        r"try again in\s+"
        r"(?:(\d+)h)?"
        r"(?:(\d+)m)?"
        r"(?:(\d+(?:\.\d+)?)s)?",
        text,
        re.I
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

    reset_match = re.search(
        r"(?:X-RateLimit-Reset|"
        r"x-ratelimit-reset)"
        r"[^0-9]{0,20}"
        r"(\d{13})",
        text,
        re.I
    )

    if reset_match:

        try:

            seconds = (
                int(
                    int(
                        reset_match.group(1)
                    )
                    / 1000
                    - time.time()
                )
            )

            if seconds > 0:
                return seconds + 10

        except Exception:
            pass

    return default


# =========================================================
# VK USER NAME
# =========================================================

def get_vk_user_name(
    user_id
):

    if not user_id:
        return None

    cached = user_names.get(
        str(user_id)
    )

    if (
        cached
        and
        time.time()
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
        and
        time.time()
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

        database_chat_id = (
            db_chat_id(chat_id)
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

        supabase.table(
            "bot_chat_memory"
        ).insert({

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

        database_chat_id = (
            db_chat_id(chat_id)
        )

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
                database_chat_id
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


# =========================================================
# NOT MY FEATURE
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
    text
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
        low
    ).strip()

    words = [
        w
        for w in stripped.split()
        if w
    ]

    if (
        not words
        or len(words) > 3
    ):

        return False

    return any(
        word.startswith(prefix)
        for word in words
        for prefix in MENU_BUTTON_PREFIXES
    )


def get_chat_message_count(
    chat_id
):

    try:

        database_chat_id = (
            db_chat_id(chat_id)
        )

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

        database_chat_id = (
            db_chat_id(chat_id)
        )

        fingerprint = (
            knowledge_fingerprint(
                database_chat_id,
                knowledge
            )
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

        supabase.table(
            "bot_knowledge"
        ).insert({

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


def get_knowledge(
    chat_id
):

    try:

        database_chat_id = (
            db_chat_id(chat_id)
        )

        result = (
            supabase
            .table("bot_knowledge")
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

        normalized = (
            normalize_text(
                fact
            ).lower()
        )

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

        database_chat_id = (
            db_chat_id(chat_id)
        )

        database_user_id = (
            db_user_id(user_id)
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

        final_memory = (
            merge_memory(
                old_memory,
                memory
            )[:3000]
        )

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
# EXPLICIT MEMORY
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

        database_chat_id = (
            db_chat_id(chat_id)
        )

        database_user_id = (
            db_user_id(user_id)
        )

        result = (
            supabase
            .table("bot_users")
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

            print(
                f"USER MEMORY MISS | "
                f"chat={database_chat_id} "
                f"user={database_user_id}",
                flush=True
            )

            return None

        memory = result.data[0]

        print(
            f"USER MEMORY HIT | "
            f"chat={database_chat_id} "
            f"user={database_user_id} | "
            f"{str(memory.get('memory', ''))[:250]}",
            flush=True
        )

        return memory

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

        database_chat_id = (
            db_chat_id(chat_id)
        )

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
            return result.data[0]

        supabase.table(
            "bot_learning_state"
        ).insert({

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

        }).execute()

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

        database_chat_id = (
            db_chat_id(chat_id)
        )

        (
            supabase
            .table("bot_learning_state")
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

        database_chat_id = (
            db_chat_id(chat_id)
        )

        (
            supabase
            .table("bot_learning_state")
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

    for tag in (
        "think",
        "analysis",
        "reasoning"
    ):

        text = re.sub(
            rf"<{tag}>.*?</{tag}>",
            "",
            text,
            flags=re.DOTALL | re.IGNORECASE
        )

        text = re.sub(
            rf"<{tag}>.*$",
            "",
            text,
            flags=re.DOTALL | re.IGNORECASE
        )

    text = re.sub(
        r"^\s*(?:assistant|final)\s*:\s*",
        "",
        text,
        flags=re.IGNORECASE
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
    "possibility 1",

)


def looks_like_leaked_reasoning(
    text
):

    if not text:
        return False

    low = text.lower()

    if any(
        marker in low
        for marker in LEAKED_REASONING_MARKERS
    ):

        return True

    numbered_bold_steps = re.findall(
        r"(?:^|\n)\s*\d+\.\s*\*\*",
        text
    )

    if len(numbered_bold_steps) >= 2:
        return True

    return False


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

    if (
        reply
        and
        looks_like_leaked_reasoning(
            reply
        )
    ):

        print(
            "Groq LEAKED REASONING:",
            reply[:200],
            flush=True
        )

        raise RuntimeError(
            "Groq returned raw reasoning."
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
                    False,

                "reasoning": {
                    "exclude": True
                }

            },

            timeout=60
        )

    except Exception as e:

        raise RuntimeError(
            f"{label} request error: {e}"
        )

    if response.status_code != 200:

        raise RuntimeError(
            f"{label} HTTP "
            f"{response.status_code}: "
            f"{response.text[:1000]}"
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

            if part.get("type") == "text":

                value = (
                    part.get("text")
                    or ""
                )

                if value:
                    parts.append(value)

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

    if looks_like_leaked_reasoning(
        reply
    ):

        print(
            f"{label} LEAKED REASONING:",
            reply[:200],
            flush=True
        )

        raise RuntimeError(
            f"{label} returned raw reasoning."
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

    global learning_main_blocked_until
    global learning_backup_blocked_until
    global openrouter_blocked_until

    now = time.time()

    # 20B

    if now >= learning_backup_blocked_until:

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

                learning_backup_blocked_until = (
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

    # 120B

    if time.time() >= learning_main_blocked_until:

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

                learning_main_blocked_until = (
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

    # OpenRouter

    if (
        OPENROUTER_API_KEY
        and
        time.time()
        >= openrouter_blocked_until
    ):

        try:

            print(
                "Learning -> OpenRouter",
                flush=True
            )

            return ask_openrouter_messages(
                messages,
                LEARNING_MAX_TOKENS,
                "OpenRouter Learning"
            )

        except Exception as e:

            if is_rate_limit_error(e):

                openrouter_blocked_until = (
                    time.time()
                    +
                    get_retry_seconds(
                        e,
                        24 * 60 * 60
                    )
                )

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

            reset_learning_counter(
                chat_id
            )

            return

        text_parts = []
        known_names = {}

        for item in history:

            if item.get(
                "role"
            ) != "user":
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
                and
                name != "Участник"
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

Проанализируй только реальные сообщения участников.

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

Если пользователь прямо сообщил факт о себе,
его можно сохранить как USER-факт.

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
                            "из сообщений."
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

                if (
                    not line
                    or
                    line.upper() == "NONE"
                ):
                    continue

                # USER

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

                # CHAT

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

        database_chat_id = (
            db_chat_id(chat_id)
        )

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
            + LEARNING_RETRY_TIME
        )

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


def maybe_learn(
    chat_id
):

    count = increase_learning_counter(
        chat_id
    )

    print(
        f"LEARNING COUNTER | "
        f"chat={chat_id} | "
        f"{count}/{LEARNING_EVERY_MESSAGES}",
        flush=True
    )

    if (
        count
        < LEARNING_EVERY_MESSAGES
    ):

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

    # STAGE

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

    # KNOWLEDGE

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

    # TANK DATABASE

    tank_rows = (
        get_tank_knowledge_for_text(
            text
        )
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

            "role":
                "system",

            "content":
                (
                    "Данные из базы Tanks Blitz. "
                    "Используй их только как источник "
                    "фактов. Не придумывай ТТХ.\n"
                    +
                    "\n".join(
                        tank_lines
                    )
                )

        })

    # RECENT CHAT

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
            and
            sid == str(user_id)
            and
            content == text
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

    # PERSONAL MEMORY

    personal = get_user_memory(
        chat_id,
        user_id
    )

    if (
        personal
        and
        personal.get("memory")
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
                        "=== ЛИЧНАЯ ПАМЯТЬ "
                        "ТЕКУЩЕГО УЧАСТНИКА ===\n"
                        "Эта память относится именно "
                        "к человеку, который сейчас пишет.\n"
                        "Используй её, если вопрос "
                        "относится к сохранённому факту.\n"
                        "Не выдумывай личные факты.\n\n"
                        "ЛИЧНАЯ ПАМЯТЬ:\n"
                        +
                        personal_memory
                        +
                        "\n=== КОНЕЦ ПАМЯТИ ==="
                    )

            })

    # CURRENT MESSAGE

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

    low = (
        text.lower()
        .strip()
    )

    return (
        "?" in low
        or
        any(
            low.startswith(
                word + " "
            )
            for word in QUESTION_WORDS
        )
    )


# =========================================================
# DIRECTED TO BOT VK
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
        and
        str(
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
        word in low
        for word in (
            "бот",
            "бонус-коды",
            "бонус коды",
            "эй бот"
        )
    )


# =========================================================
# DIRECTED TO BOT TELEGRAM
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
        and
        reply_from.get("id")
        == TELEGRAM_BOT_ID
    ):

        return True

    if (
        TELEGRAM_BOT_USERNAME
        and
        (
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

    if looks_like_not_my_feature_question(
        text
    ):

        return False

    # DIRECT MESSAGE TO BOT

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

    # QUESTION

    if looks_like_question(
        text
    ):

        return True

    words = len(
        text.split()
    )

    # Более живой бот.
    #
    # Раньше:
    # 2 слова -> 10%
    # 6 слов -> 25%
    # 15 слов -> 45%
    # остальное -> 60%
    #
    # Теперь:
    # 2 слова -> 20%
    # 6 слов -> 40%
    # 15 слов -> 60%
    # длинные -> 72%

    if words <= 2:

        return (
            random.random()
            < 0.20
        )

    if words <= 6:

        return (
            random.random()
            < 0.40
        )

    if words <= 15:

        return (
            random.random()
            < 0.60
        )

    return (
        random.random()
        < 0.72
    )


# =========================================================
# AI ROUTER
# =========================================================

def ask_ai(
    chat_id,
    text,
    user_id,
    user_name
):

    global openrouter_blocked_until

    try:

        return ask_groq(
            chat_id,
            text,
            user_id,
            user_name
        )

    except Exception as groq_error:

        print(
            "Groq final error -> OpenRouter:",
            groq_error,
            flush=True
        )

        if (
            time.time()
            < openrouter_blocked_until
        ):

            raise RuntimeError(
                "Все текстовые AI "
                "временно недоступны."
            )

        try:

            return ask_openrouter(
                chat_id,
                text,
                user_id,
                user_name
            )

        except Exception as openrouter_error:

            if is_rate_limit_error(
                openrouter_error
            ):

                openrouter_blocked_until = (
                    time.time()
                    +
                    get_retry_seconds(
                        openrouter_error,
                        24 * 60 * 60
                    )
                )

            print(
                "OpenRouter final error:",
                openrouter_error,
                flush=True
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

    # 120B

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

    # 20B

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
        "Обе модели Groq "
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
        return

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
# TELEGRAM
# =========================================================

def telegram_call(
    method,
    **kwargs
):

    if not TELEGRAM_API:

        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN "
            "не установлен"
        )

    response = requests.post(
        f"{TELEGRAM_API}/{method}",
        json=kwargs,
        timeout=30
    )

    data = response.json()

    if not data.get("ok"):

        raise RuntimeError(
            f"Telegram {method}: "
            f"{data}"
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

                if (
                    now
                    - item["last"]
                    < 20 * 60
                ):

                    continue

                with activity_lock:

                    if key in active_chats:

                        active_chats[key][
                            "last"
                        ] = now

                # 35% шанс оживить чат

                if random.random() > 0.35:
                    continue

                prompt = random.choice([

                    (
                        "В чате давно тихо. "
                        "Если есть естественная причина "
                        "оживить разговор, напиши одну "
                        "короткую живую реплику. "
                        "Можно пошутить или слегка "
                        "подъебнуть ситуацию. "
                        "Не говори, что ты бот."
                    ),

                    (
                        "В чате тишина. "
                        "Придумай короткую естественную "
                        "реплику обычного участника. "
                        "Можно использовать немного "
                        "разговорного мата, если подходит."
                    ),

                    (
                        "Народ молчит. "
                        "Оживи чат одной короткой "
                        "эмоциональной фразой. "
                        "Без официальщины."
                    )

                ])

                try:

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
# HEALTH
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

        "personality":
            "alive",

        "profanity":
            True,

        "openrouter":
            bool(
                OPENROUTER_API_KEY
            ),

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

        if (
            VK_GROUP_SECRET
            and
            data.get("secret")
            != VK_GROUP_SECRET
        ):

            return "invalid secret", 403

        event_type = data.get(
            "type"
        )

        if event_type == "confirmation":

            return (
                VK_CONFIRMATION_CODE
            )

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
            data["object"]["message"]
        )

        peer_id = message[
            "peer_id"
        ]

        sender_id = (
            message.get("from_id")
            or
            message.get("user_id")
        )

        if (
            sender_id
            and
            int(peer_id)
            == int(sender_id)
        ):

            return "ok"

        if not sender_id:
            return "ok"

        # Игнорируем сообщения других сообществ/ботов.

        if int(sender_id) < 0:
            return "ok"

        chat_id = int(
            peer_id
        )

        register_active_chat(
            "vk",
            peer_id
        )

        text = (
            message.get("text")
            or ""
        ).strip()

        user_name = (
            get_vk_user_name(
                sender_id
            )
        )

        # Только текст.

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
            or
            sender_id is None
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
            or
            message.get("caption")
            or
            ""
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
                "Telegram: Render URL "
                "не найден — webhook "
                "не установлен.",
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
        "🔥 Personality: ALIVE",
        flush=True
    )

    print(
        "🤬 Profanity: ENABLED",
        flush=True
    )

    print(
        "😂 Humor: ENABLED",
        flush=True
    )

    print(
        "🧠 Self-learning: ENABLED",
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
