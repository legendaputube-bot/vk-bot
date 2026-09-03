import os
import re
import time
import hashlib
import random
import threading
from collections import deque

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


# =========================================================
# SUPABASE
# =========================================================

supabase = create_client(
    SUPABASE_URL,
    SUPABASE_SECRET_KEY
)

print("Supabase подключён:", bool(supabase), flush=True)


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
# TOKEN LIMITS
# =========================================================

# Ответы
GROQ_MAX_TOKENS = 250

# Обучение
LEARNING_MAX_TOKENS = 300

# Sonar
SONAR_MAX_TOKENS = 150


# =========================================================
# MEMORY
# =========================================================

# Короткая память одного разговора
CHAT_MEMORY_LIMIT = 40

# Сколько сообщений между обучениями
LEARNING_EVERY_MESSAGES = 40

# Максимум знаний, которые достаём из базы
KNOWLEDGE_LIMIT = 15

# Максимум участников в контексте
USER_MEMORY_LIMIT = 10


# =========================================================
# CACHE
# =========================================================

SONAR_CACHE_TIME = 30 * 60
NAME_CACHE_TIME = 24 * 60 * 60

EVENT_CACHE_TIME = 30 * 60
EVENT_CACHE_LIMIT = 2000

main_blocked_until = 0
backup_blocked_until = 0

user_names = {}
sonar_cache = {}
processed_events = {}


# =========================================================
# APP
# =========================================================

app = Flask(__name__)
groq = Groq(api_key=GROQ_API_KEY)


# =========================================================
# CHILD / PERSONALITY
# =========================================================

DEVELOPMENT_STAGES = {
    1: "Ты совсем молодой. Ты много наблюдаешь и мало вмешиваешься.",
    2: "Ты уже немного освоился. Начинаешь понимать шутки и привычки людей.",
    3: "Ты уже свой в чате. Хорошо понимаешь атмосферу и можешь иногда подколоть.",
    4: "Ты опытный участник чата. Хорошо помнишь людей, темы и локальные приколы."
}


SYSTEM_PROMPT = """
Ты — живой AI-участник сообщества «Бонус коды Tanks Blitz».

Ты не являешься модератором и не должен притворяться администрацией.
Ты обычный участник общего чата.

Твоя главная тема — Tanks Blitz, но ты умеешь разговаривать и на обычные человеческие темы.

==================================================
ХАРАКТЕР
==================================================

Ты постепенно развиваешься благодаря общению с людьми.

Ты:
- живой;
- умный;
- любопытный;
- иногда дерзкий;
- с чувством юмора;
- можешь слегка подколоть друга;
- не являешься сухим помощником;
- умеешь поддержать разговор;
- умеешь иногда просто промолчать.

Не шути в каждом сообщении.

Не превращай каждый ответ в мем.

Если человек серьёзный — будь серьёзным.
Если человек шутит — можешь поддержать.
Если человек злится — не провоцируй его.

==================================================
 ОБЩЕНИЕ
==================================================

Не отвечай шаблонно.

Не начинай постоянно с:
«Конечно».
«Разумеется».
«Хороший вопрос».

Не повторяй сообщение пользователя.

Короткий вопрос → короткий ответ.

Сложный вопрос → подробный ответ.

Если человек просто общается, поддерживай естественный разговор.

Иногда можно ответить одной фразой.

==================================================
 РАЗВИТИЕ
==================================================

Ты можешь использовать долговременную память.

Но память не является абсолютной истиной.

Если сохранённое знание выглядит сомнительным — не выдавай его за факт.

Ты постепенно узнаёшь:
- людей;
- их интересы;
- стиль общения;
- шутки;
- темы;
- события в чате;
- полезную информацию о Tanks Blitz.

Не говори человеку:
«Я записал это в память».

Не рассказывай о внутренней системе памяти.

==================================================
 TANKS BLITZ
==================================================

Не придумывай игровые характеристики.

Не смешивай Tanks Blitz и World of Tanks PC.

Если точных данных нет — скажи об этом.

Если переданы актуальные данные из поиска — используй их.

==================================================
 ЛЮДИ
==================================================

Не придумывай личные факты.

Не раскрывай личную информацию участников.

Не упоминай старые сведения без причины.

Имя используй редко и естественно.

==================================================
 ПРАВИЛА
==================================================

Ты знаешь правила сообщества, но не являешься модератором.

Не говори:
«Я тебя замучу».
«Я тебя забаню».
«Я удалю сообщение».

Вместо этого можешь спокойно сказать, что лучше не нарушать правила чата.

==================================================
 ГЛАВНОЕ
==================================================

Ты не обязан отвечать на каждую реплику.

Лучше иногда промолчать, чем писать бесполезный ответ.

Твоя задача — быть настоящим участником разговора.
"""


# =========================================================
# EVENT PROTECTION
# =========================================================

def already_processed(event_id):
    if not event_id:
        return False

    now = time.time()

    expired = [
        key for key, saved in processed_events.items()
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

        processed_events.pop(oldest, None)

    return False


# =========================================================
# RATE LIMIT
# =========================================================

def is_rate_limit_error(error):
    text = str(error).lower()

    return any(x in text for x in (
        "429",
        "rate limit",
        "rate_limit_exceeded",
        "tokens per day",
        "tpd",
        "too many requests"
    ))


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

    total = hours * 3600 + minutes * 60 + seconds

    return int(total) + 10 if total > 0 else default


# =========================================================
# VK NAME
# =========================================================

def get_vk_user_name(user_id):
    if not user_id:
        return None

    cached = user_names.get(str(user_id))

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

        users = response.json().get("response", [])

        if not users:
            return None

        user = users[0]

        first = user.get("first_name", "").strip()
        last = user.get("last_name", "").strip()

        name = f"{first} {last}".strip()

        if not name:
            return None

        user_names[str(user_id)] = (
            time.time(),
            name
        )

        return name

    except Exception as e:
        print("VK name error:", e, flush=True)
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
        supabase.table(
            "bot_chat_memory"
        ).insert({
            "chat_id": str(chat_id),
            "speaker_id": str(speaker_id or ""),
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


def get_chat_memory(chat_id, limit=CHAT_MEMORY_LIMIT):
    try:
        response = (
            supabase
            .table("bot_chat_memory")
            .select(
                "speaker_name, role, content"
            )
            .eq("chat_id", str(chat_id))
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


# =========================================================
# KNOWLEDGE
# =========================================================

def save_knowledge(chat_id, knowledge):
    if not knowledge:
        return

    knowledge = knowledge.strip()

    if len(knowledge) < 5:
        return

    try:
        supabase.table(
            "bot_knowledge"
        ).insert({
            "chat_id": str(chat_id),
            "knowledge": knowledge[:2000],
            "importance": 1
        }).execute()

        print(
            "KNOWLEDGE SAVED:",
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
            .select("knowledge, importance")
            .eq("chat_id", str(chat_id))
            .order("importance", desc=True)
            .order("created_at", desc=True)
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

def save_user_memory(
    chat_id,
    user_id,
    name,
    memory
):
    if not chat_id or not user_id or not memory:
        return

    try:
        existing = (
            supabase
            .table("bot_users")
            .select("id")
            .eq("chat_id", str(chat_id))
            .eq("user_id", str(user_id))
            .limit(1)
            .execute()
        )

        data = {
            "chat_id": str(chat_id),
            "user_id": str(user_id),
            "name": name or "",
            "memory": memory[:3000],
            "updated_at": "now()"
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

    except Exception as e:
        print(
            "User memory save error:",
            e,
            flush=True
        )


def get_user_memory(chat_id, user_id):
    try:
        response = (
            supabase
            .table("bot_users")
            .select("name, memory")
            .eq("chat_id", str(chat_id))
            .eq("user_id", str(user_id))
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
            .eq("chat_id", str(chat_id))
            .limit(1)
            .execute()
        )

        if response.data:
            return response.data[0]

        supabase.table(
            "bot_learning_state"
        ).insert({
            "chat_id": str(chat_id),
            "messages_since_learning": 0,
            "development_stage": 1,
            "personality": ""
        }).execute()

        return {
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
            "messages_since_learning": 0,
            "development_stage": 1,
            "personality": ""
        }


def increase_learning_counter(chat_id):
    state = get_learning_state(chat_id)

    count = int(
        state.get(
            "messages_since_learning",
            0
        )
    ) + 1

    try:
        supabase.table(
            "bot_learning_state"
        ).update({
            "messages_since_learning": count
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
# LEARNING
# =========================================================

def perform_learning(chat_id):
    """
    Бот периодически анализирует последние сообщения
    и сохраняет только полезный опыт.
    """

    try:
        state = get_learning_state(chat_id)

        messages = get_chat_memory(
            chat_id,
            limit=60
        )

        if len(messages) < 10:
            return

        text = "\n".join(
            f"{m.get('speaker_name') or 'Участник'}: "
            f"{m.get('content', '')}"
            for m in messages
        )

        prompt = f"""
Ты помогаешь AI-участнику постепенно учиться на общении.

Проанализируй последние сообщения чата.

Не пересказывай разговор.

Найди только действительно полезные вещи:

1. Важные факты о происходящем в этом чате.
2. Повторяющиеся локальные шутки или темы.
3. Интересы участников, если они явно понятны.
4. Полезные особенности общения.
5. Что стоит запомнить для будущих разговоров.

Не сохраняй:
- случайную болтовню;
- оскорбления;
- непроверенные слухи;
- пароли;
- личные данные;
- чувствительную информацию;
- каждую мелочь.

Верни максимум 5 коротких пунктов.

Если нечего запоминать — напиши:
НЕТ

Сообщения:

{text}
"""

        messages_for_ai = [
            {
                "role": "system",
                "content": (
                    "Ты аккуратный модуль долговременного "
                    "обучения AI-бота."
                )
            },
            {
                "role": "user",
                "content": prompt
            }
        ]

        learned = ask_model(
            BACKUP_MODEL,
            messages_for_ai,
            max_tokens=LEARNING_MAX_TOKENS
        )

        learned = learned.strip()

        if learned.upper() == "НЕТ":
            return

        for line in learned.splitlines():
            line = re.sub(
                r"^[\-\*\d\.\)\s]+",
                "",
                line
            ).strip()

            if line:
                save_knowledge(
                    chat_id,
                    line
                )

        stage = int(
            state.get(
                "development_stage",
                1
            )
        )

        if stage < 4:
            # Очень медленное взросление
            total_messages = len(messages)

            if total_messages >= 300:
                stage = 2

            if total_messages >= 1000:
                stage = 3

            if total_messages >= 3000:
                stage = 4

        supabase.table(
            "bot_learning_state"
        ).update({
            "messages_since_learning": 0,
            "development_stage": stage,
            "last_learning_at": "now()"
        }).eq(
            "chat_id",
            str(chat_id)
        ).execute()

        print(
            f"BOT LEARNED. Stage={stage}",
            flush=True
        )

    except Exception as e:
        print(
            "Learning error:",
            e,
            flush=True
        )


def maybe_learn(chat_id):
    count = increase_learning_counter(chat_id)

    if count < LEARNING_EVERY_MESSAGES:
        return

    # Запускаем обучение отдельно,
    # чтобы VK не ждал его завершения.
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
            "role": "system",
            "content": SYSTEM_PROMPT
        }
    ]

    state = get_learning_state(chat_id)

    stage = int(
        state.get(
            "development_stage",
            1
        )
    )

    messages.append({
        "role": "system",
        "content": (
            "Текущая стадия развития:\n"
            + DEVELOPMENT_STAGES.get(
                stage,
                DEVELOPMENT_STAGES[1]
            )
        )
    })

    knowledge = get_knowledge(chat_id)

    if knowledge:
        knowledge_text = "\n".join(
            f"- {item['knowledge']}"
            for item in knowledge
            if item.get("knowledge")
        )

        messages.append({
            "role": "system",
            "content": (
                "Долговременная память чата. "
                "Используй только если относится "
                "к текущему разговору:\n"
                + knowledge_text
            )
        })

    personal = get_user_memory(
        chat_id,
        user_id
    )

    if personal:
        messages.append({
            "role": "system",
            "content": (
                "Что известно об этом участнике:\n"
                f"{personal.get('memory', '')}"
            )
        })

    history = get_chat_memory(
        chat_id,
        CHAT_MEMORY_LIMIT
    )

    if history:
        for item in history:
            role = item.get("role")

            if role not in (
                "user",
                "assistant"
            ):
                continue

            name = item.get(
                "speaker_name"
            ) or "Участник"

            content = item.get(
                "content",
                ""
            )

            if role == "user":
                messages.append({
                    "role": "user",
                    "content": f"{name}: {content}"
                })

            else:
                messages.append({
                    "role": "assistant",
                    "content": content
                })

    messages.append({
        "role": "user",
        "content": f"{user_name or 'Участник'}: {text}"
    })

    return messages


# =========================================================
# GROQ
# =========================================================

def ask_model(
    model,
    messages,
    max_tokens=GROQ_MAX_TOKENS
):
    completion = groq.chat.completions.create(
        model=model,
        messages=messages,
        max_tokens=max_tokens
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

    reply = completion.choices[0].message.content

    if not reply:
        raise RuntimeError(
            "Groq returned empty response."
        )

    reply = re.sub(
        r"<think>.*?</think>",
        "",
        reply,
        flags=re.DOTALL
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

    # 120B
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
                    60 * 60
                )

                main_blocked_until = (
                    time.time() + cooldown
                )

            print(
                "120B error:",
                e,
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
                messages
            )

        except Exception as e:

            if is_rate_limit_error(e):
                cooldown = get_retry_seconds(
                    e,
                    10 * 60
                )

                backup_blocked_until = (
                    time.time() + cooldown
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
    text = text.lower()

    return any(
        word in text
        for word in WEB_WORDS
    )


def cache_key(text):
    return hashlib.sha256(
        text.lower().strip().encode()
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

        if time.time() - saved < SONAR_CACHE_TIME:
            return answer

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
                        "Не смешивай с World of Tanks PC. "
                        "Не выдумывай данные."
                    )
                },
                {
                    "role": "user",
                    "content": text
                }
            ],
            "max_tokens": SONAR_MAX_TOKENS
        },
        timeout=30
    )

    if response.status_code != 200:
        raise RuntimeError(
            f"Sonar HTTP {response.status_code}"
        )

    data = response.json()

    answer = (
        data["choices"][0]["message"]["content"]
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
            found = ask_sonar(text)

            prompt = (
                f"Вопрос пользователя:\n{text}\n\n"
                f"Актуальные данные:\n{found}\n\n"
                "Ответь естественно и коротко. "
                "Не упоминай API, Sonar или Perplexity."
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
                "text": transcript.strip(),
                "url": None
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

            result = groq.audio.transcriptions.create(
                file=file,
                model=WHISPER_MODEL,
                response_format="text"
            )

        return str(result).strip()

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

            url = size.get("url")

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
        "Посмотри этот скриншот из Tanks Blitz "
        "и скажи, что на нём происходит."
    )

    messages = build_chat_context(
        chat_id,
        user_id,
        user_name,
        prompt_text
    )

    # Заменяем последнее сообщение
    messages[-1] = {
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

    completion = groq.chat.completions.create(
        model=VISION_MODEL,
        messages=messages,
        max_tokens=GROQ_MAX_TOKENS
    )

    reply = completion.choices[0].message.content

    if not reply:
        raise RuntimeError(
            "Vision returned empty response."
        )

    reply = re.sub(
        r"<think>.*?</think>",
        "",
        reply,
        flags=re.DOTALL
    ).strip()

    return reply


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
# SHOULD BOT ANSWER?
# =========================================================

def should_answer(text):

    text = text.strip().lower()

    if not text:
        return False

    # Явно не отвечаем на бессмысленный мусор
    if len(text) <= 1:
        return False

    return True


# =========================================================
# SELF INITIATED CHAT
# =========================================================

active_chats = {}
activity_lock = threading.Lock()


def register_active_chat(peer_id):

    with activity_lock:
        active_chats[str(peer_id)] = time.time()


def activity_loop():

    while True:

        try:
            now = time.time()

            with activity_lock:
                chats = dict(active_chats)

            for chat_id, last_message in chats.items():

                # 20 минут тишины
                if now - last_message < 20 * 60:
                    continue

                # Чтобы не спамил постоянно
                with activity_lock:
                    active_chats[chat_id] = now

                prompts = [
                    "В чате уже давно тихо. Напиши короткую живую фразу, чтобы оживить разговор.",
                    "Народ давно молчит. Сам придумай естественную короткую реплику для оживления чата.",
                    "В чате тишина. Напиши что-нибудь живое и слегка смешное, как обычный участник."
                ]

                prompt = random.choice(prompts)

                try:

                    reply = ask_groq(
                        chat_id,
                        prompt,
                        "system",
                        None
                    )

                    if reply:
                        send_message(
                            int(chat_id),
                            reply
                        )

                        save_chat_message(
                            chat_id,
                            "system",
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

        # -------------------------------------------------
        # SECRET
        # -------------------------------------------------

        if (
            VK_GROUP_SECRET
            and data.get("secret")
            != VK_GROUP_SECRET
        ):
            return "invalid secret", 403

        event_type = data.get("type")

        # -------------------------------------------------
        # CONFIRMATION
        # -------------------------------------------------

        if event_type == "confirmation":
            return VK_CONFIRMATION_CODE

        if event_type != "message_new":
            return "ok"

        # -------------------------------------------------
        # DUPLICATE
        # -------------------------------------------------

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

        # -------------------------------------------------
        # ЛИЧНЫЕ СООБЩЕНИЯ
        # -------------------------------------------------

        # В личке peer_id обычно равен sender_id.
        # Бот полностью игнорирует личку.

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

        # -------------------------------------------------
        # ТОЛЬКО ГРУППОВЫЕ ЧАТЫ
        # -------------------------------------------------

        # Если это не беседа,
        # дополнительно не вмешиваемся.

        if not sender_id:
            return "ok"

        chat_id = str(peer_id)

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

        # -------------------------------------------------
        # VOICE
        # -------------------------------------------------

        voice = get_voice(
            message
        )

        if voice:

            if voice["text"]:

                recognized = voice["text"]

            else:

                try:
                    recognized = transcribe_voice(
                        voice["url"]
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

            save_chat_message(
                chat_id,
                "bot",
                "Бот",
                "assistant",
                reply
            )

            send_message(
                peer_id,
                reply
            )

            return "ok"

        # -------------------------------------------------
        # IMAGE
        # -------------------------------------------------

        image_url = get_image(
            message
        )

        if image_url:

            save_chat_message(
                chat_id,
                sender_id,
                user_name,
                "user",
                text or "[скриншот]"
            )

            maybe_learn(
                chat_id
            )

            try:

                reply = analyze_image_then_groq(
                    image_url,
                    text,
                    chat_id,
                    str(sender_id),
                    user_name
                )

            except Exception as e:

                print(
                    "Image error:",
                    e,
                    flush=True
                )

                return "ok"

            save_chat_message(
                chat_id,
                "bot",
                "Бот",
                "assistant",
                reply
            )

            send_message(
                peer_id,
                reply
            )

            return "ok"

        # -------------------------------------------------
        # EMPTY
        # -------------------------------------------------

        if not text:
            return "ok"

        # -------------------------------------------------
        # SAVE EVERY NORMAL MESSAGE
        # -------------------------------------------------

        save_chat_message(
            chat_id,
            sender_id,
            user_name,
            "user",
            text
        )

        # -------------------------------------------------
        # LEARNING
        # -------------------------------------------------

        maybe_learn(
            chat_id
        )

        # -------------------------------------------------
        # AI
        # -------------------------------------------------

        if not should_answer(
            text
        ):
            return "ok"

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

        # -------------------------------------------------
        # SAVE BOT RESPONSE
        # -------------------------------------------------

        save_chat_message(
            chat_id,
            "bot",
            "Бот",
            "assistant",
            reply
        )

        # -------------------------------------------------
        # SEND
        # -------------------------------------------------

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

    # Запускаем самостоятельную активность
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
