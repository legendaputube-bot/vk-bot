import os
import re
import time
import hashlib
from collections import defaultdict, deque

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

PERPLEXITY_API_KEY = os.environ.get(
    "PERPLEXITY_API_KEY", ""
)

PERPLEXITY_MODEL = os.environ.get(
    "PERPLEXITY_MODEL", ""
)
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip()
SUPABASE_SECRET_KEY = os.environ.get("SUPABASE_SECRET_KEY", "").strip()

if SUPABASE_URL and not SUPABASE_URL.startswith(("http://", "https://")):
    SUPABASE_URL = "https://" + SUPABASE_URL

supabase = create_client(
    SUPABASE_URL,
    SUPABASE_SECRET_KEY
)
VK_API = "https://api.vk.com/method"
VK_VERSION = "5.199"

MAIN_MODEL = "openai/gpt-oss-120b"
BACKUP_MODEL = "openai/gpt-oss-20b"

# Уменьшенный расход токенов
GROQ_MAX_TOKENS = 220
SONAR_MAX_TOKENS = 300
IMAGE_MAX_TOKENS = 200

# Память: user → assistant → user → assistant
MEMORY_LIMIT = 4

# Кеш Sonar
SONAR_CACHE_TIME = 30 * 60

# Кеш имён VK
NAME_CACHE_TIME = 24 * 60 * 60

# Защита от повторных событий VK
EVENT_CACHE_TIME = 30 * 60
EVENT_CACHE_LIMIT = 1000

# Если 120B получила дневной лимит
MAIN_DEFAULT_COOLDOWN = 60 * 60

# Если 20B получила лимит и VK/Groq дали
# время повторной попытки
BACKUP_DEFAULT_COOLDOWN = 10 * 60


# =========================================================
# SYSTEM PROMPT
# =========================================================

SYSTEM_PROMPT = (
    "Ты — живой, дерзкий и языкастый бот сообщества ВКонтакте "
    "про Tanks Blitz.\n\n"

    "Твоя основная тема — Tanks Blitz. Если сообщение вообще "
    "не связано с игрой или текущим разговором, коротко напомни, "
    "что здесь говорят про танки.\n\n"

    "НЕ ВЫДУМЫВАЙ факты. Не придумывай названия танков, ветки, "
    "характеристики, урон, броню, пробитие, скорость, перезарядку, "
    "карты, режимы, события, бонус-коды или цифры.\n\n"

    "Не смешивай Tanks Blitz с World of Tanks PC.\n\n"

    "Если переданы данные поиска или анализа изображения, "
    "используй только их. Не добавляй неподтверждённые факты.\n\n"

    "Если точных данных нет — честно скажи об этом.\n\n"

    "Отвечай коротко, живо и по делу. Если нужен список — "
    "максимум 3 пункта.\n\n"

    "Можно использовать лёгкую иронию и подколы, "
    "но без настоящих оскорблений и переходов на личности.\n\n"

    "Учитывай историю диалога и понимай короткие продолжения "
    "вроде «а этот?», «а почему?», «а если так?». "
)


# =========================================================
# APP
# =========================================================

app = Flask(__name__)
groq = Groq(api_key=GROQ_API_KEY)


# =========================================================
# MEMORY / CACHE
# =========================================================

user_memory = defaultdict(
    lambda: deque(maxlen=MEMORY_LIMIT)
)

user_names = {}
sonar_cache = {}
processed_events = {}

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
        key for key, saved in processed_events.items()
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
    "кто", "что", "где", "когда", "зачем", "почему",
    "как", "какой", "какая", "какие", "какое",
    "какого", "какую", "каких", "сколько", "куда",
    "откуда", "можно", "нужно", "стоит", "будет",
    "есть", "подскажешь", "посоветуешь", "скажешь",
    "знаешь", "думаешь"
}

FOLLOWUPS = (
    "а почему", "а зачем", "а как", "а какой",
    "а какая", "а какие", "а какое", "а где",
    "а когда", "а сколько", "а этот", "а эта",
    "а эти", "а оно", "а он", "а она", "а там",
    "а на", "а если", "а что если", "а можно",
    "а нужно", "а стоит", "и что", "и как",
    "и какой", "тогда как", "тогда что", "ну а",
    "не понял", "не понимаю", "объясни",
    "расскажи", "подробнее", "почему так"
)

IGNORED = {
    "ок", "окей", "ага", "угу", "да", "нет",
    "лол", "ахах", "ахаха", "пон", "ясно",
    "спс", "спасибо", "благодарю", "+", "++",
    "👍", "👌", "😂", "🤣"
}

TANK_WORDS = (
    "танк", "танка", "танке", "танки", "танков",
    "блиц", "blitz", "урон", "брон", "пробит",
    "оруд", "калибр", "хп", "перезаряд", "скорост",
    "точност", "ветк", "прокач", "экипаж", "модул",
    "снаряд", "голд", "серебр", "опыт", "карта",
    "карты", "бой", "бои", "ивент", "событи",
    "патч", "обновлен", "обновление", "нерф",
    "бафф", "промокод", "бонус-код", "бонус код",
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

    # Например: аааааааа / )))))))))
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

    if any(text.startswith(x) for x in FOLLOWUPS):
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

    # Вопросы работают даже без знака ?
    if looks_like_question(text):
        return True

    # Любое нормальное сообщение по Tanks Blitz
    # тоже может получить ответ.
    if is_tanks_message(text):
        return True

    # Короткое продолжение уже начатого диалога.
    if user_id and user_memory.get(user_id):
        if len(text) <= 80:
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

        users = response.json().get("response", [])

        if not users:
            return None

        user = users[0]

        first = user.get(
            "first_name", ""
        ).strip()

        last = user.get(
            "last_name", ""
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
        print("VK name error:", e, flush=True)
        return None


# =========================================================
# MEMORY
# =========================================================

def add_memory(user_id, role, content):
    if user_id and content:
        user_memory[user_id].append({
            "role": role,
            "content": content
        })


def get_memory(user_id):
    if not user_id:
        return []

    return list(
        user_memory.get(user_id, [])
    )


def cleanup_memory():
    limit = 5000

    if len(user_memory) <= limit:
        return

    for _ in range(
        len(user_memory) - limit
    ):
        try:
            del user_memory[
                next(iter(user_memory))
            ]
        except StopIteration:
            break


# =========================================================
# GROQ MESSAGES
# =========================================================

def build_messages(
    text,
    user_id=None,
    user_name=None
):
    messages = [{
        "role": "system",
        "content": SYSTEM_PROMPT
    }]

    if user_name:
        messages.append({
            "role": "system",
            "content": (
                f"Имя пользователя: {user_name}. "
                "Используй имя редко и естественно."
            )
        })

    history = get_memory(user_id)

    if history:
        messages.extend(history)

    messages.append({
        "role": "user",
        "content": text
    })

    return messages


# =========================================================
# GROQ REQUEST
# =========================================================

def ask_model(model, messages):
    completion = (
        groq
        .chat
        .completions
        .create(
            model=model,
            messages=messages,
            max_tokens=GROQ_MAX_TOKENS
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
            "prompt=", getattr(
                usage, "prompt_tokens", None
            ),
            "completion=", getattr(
                usage, "completion_tokens", None
            ),
            "total=", getattr(
                usage, "total_tokens", None
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

    return reply.strip()


# =========================================================
# GROQ ROUTER
# =========================================================

def ask_groq(
    text,
    user_id=None,
    user_name=None
):
    global main_blocked_until
    global backup_blocked_until

    messages = build_messages(
        text,
        user_id,
        user_name
    )

    now = time.time()

    # -----------------------------------------------------
    # 120B
    # -----------------------------------------------------

    if now >= main_blocked_until:
        try:
            print(
                "Groq → 120B",
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
                    time.time() + cooldown
                )

                print(
                    f"120B limit → "
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
                "Groq → 20B",
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
                    time.time() + cooldown
                )

                print(
                    f"20B limit → "
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

WEB_WORDS = (
    "характеристик", "урон", "брон", "скорост",
    "точност", "перезаряд", "хп", "пробит",
    "калибр", "оруд", "танк", "танка", "танке",
    "танков", "ветк", "прокач", "обновлен",
    "обновление", "патч", "ивент", "событи",
    "новый танк", "новые танки", "актуальн",
    "сейчас", "сегодня", "последн", "добавили",
    "убрали", "изменили", "нерф", "бафф",
    "промокод", "бонус код", "бонус-код", "код"
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
            print(
                "Sonar → cache",
                flush=True
            )
            return answer

    print(
        "Sonar → search",
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
# SONAR → GROQ
# =========================================================

def ask_sonar_then_groq(
    text,
    user_id=None,
    user_name=None
):
    found = ask_sonar(text)

    prompt = (
        "Ответь пользователю на основе найденных данных.\n"
        f"Вопрос: {text}\n"
        f"Данные: {found}\n\n"
        "Дай один короткий ответ. "
        "Не упоминай Sonar, Perplexity, Groq или API. "
        "Не добавляй неподтверждённые факты."
    )

    return ask_groq(
        prompt,
        user_id,
        user_name
    )


def ask_ai(
    text,
    user_id=None,
    user_name=None
):
    if needs_sonar(text):
        try:
            print(
                "ROUTER → Sonar → Groq",
                flush=True
            )

            return ask_sonar_then_groq(
                text,
                user_id,
                user_name
            )

        except Exception as e:
            print(
                "Sonar error:",
                e,
                flush=True
            )

    print(
        "ROUTER → Groq",
        flush=True
    )

    return ask_groq(
        text,
        user_id,
        user_name
    )


# =========================================================
# VK SEND
# =========================================================

def send_message(peer_id, text):
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
        "attachments", []
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

        url = audio.get("link_ogg")

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

    path = "/tmp/voice.ogg"

    with open(path, "wb") as file:
        file.write(data)

    with open(path, "rb") as file:
        result = groq.audio.transcriptions.create(
            file=file,
            model="whisper-large-v3-turbo",
            response_format="text"
        )

    return str(result).strip()


# =========================================================
# IMAGE
# =========================================================

def get_image(message):
    best = None
    best_area = 0

    for attachment in message.get(
        "attachments", []
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
            "sizes", []
        ):
            url = size.get("url")

            if not url:
                continue

            area = (
                size.get("width", 0)
                * size.get("height", 0)
            )

            if area > best_area:
                best = url
                best_area = area

    return best


def analyze_image(
    image_url,
    text
):
    if not PERPLEXITY_API_KEY:
        raise RuntimeError(
            "PERPLEXITY_API_KEY не установлен."
        )

    if not PERPLEXITY_MODEL:
        raise RuntimeError(
            "PERPLEXITY_MODEL не установлен."
        )

    content = [
        {
            "type": "text",
            "text": (
                "Проанализируй скриншот Tanks Blitz "
                "и ответь только на вопрос пользователя.\n"
                "Не описывай весь скриншот.\n"
                "Не придумывай то, чего не видно.\n"
                f"Вопрос: {text}"
            )
        },
        {
            "type": "image_url",
            "image_url": {
                "url": image_url
            }
        }
    ]

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
                        "Анализируй только изображение "
                        "Tanks Blitz. Не выдумывай."
                    )
                },
                {
                    "role": "user",
                    "content": content
                }
            ],
            "max_tokens": IMAGE_MAX_TOKENS
        },
        timeout=45
    )

    if response.status_code != 200:
        raise RuntimeError(
            f"Image HTTP {response.status_code}"
        )

    data = response.json()

    result = (
        data["choices"][0]
        ["message"]
        ["content"]
        .strip()
    )

    if not result:
        raise RuntimeError(
            "Пустой анализ изображения."
        )

    return result[:3000]


def analyze_image_then_groq(
    image_url,
    text,
    user_id=None,
    user_name=None
):
    analysis = analyze_image(
        image_url,
        text
    )

    prompt = (
        "Ответь пользователю по анализу скриншота.\n"
        f"Вопрос: {text}\n"
        f"Анализ: {analysis}\n\n"
        "Один короткий ответ. "
        "Не упоминай внутреннюю систему, "
        "Perplexity, Sonar, Groq или API. "
        "Не придумывай отсутствующие данные."
    )

    return ask_groq(
        prompt,
        user_id,
        user_name
    )


# =========================================================
# ERROR MESSAGE
# =========================================================

def ai_error_message(error):
    if "обе модели" in str(error).lower():
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
            print(
                "Duplicate VK event.",
                flush=True
            )
            return "ok"

        message = (
            data["object"]["message"]
        )

        peer_id = message["peer_id"]

        sender_id = message.get(
            "from_id"
        )

        if not sender_id:
            sender_id = message.get(
                "user_id"
            )

        user_id = str(
            sender_id or peer_id
        )

        text = message.get(
            "text",
            ""
        ).strip()

        # -------------------------------------------------
        # GREETING
        # -------------------------------------------------

        if is_greeting(text):
            send_message(
                peer_id,
                greeting_response(text)
            )
            return "ok"

        # -------------------------------------------------
        # VOICE
        # -------------------------------------------------

        voice = get_voice(message)

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

            reply = ask_ai(
                recognized,
                user_id,
                user_name
            )

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

            cleanup_memory()

            send_message(
                peer_id,
                reply
            )

            return "ok"

        # -------------------------------------------------
        # IMAGE
        # -------------------------------------------------

        image_url = get_image(message)

        if image_url:
            if not text:
                print(
                    "Image without question ignored.",
                    flush=True
                )
                return "ok"

            if not should_use_ai(
                text,
                user_id
            ):
                print(
                    "Image text ignored.",
                    flush=True
                )
                return "ok"

            user_name = get_vk_user_name(
                sender_id
            )

            reply = analyze_image_then_groq(
                image_url,
                text,
                user_id,
                user_name
            )

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

            cleanup_memory()

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
        # LOCAL ROUTER
        # -------------------------------------------------

        if not should_use_ai(
            text,
            user_id
        ):
            print(
                "Ignored locally → 0 AI tokens.",
                flush=True
            )
            return "ok"

        # -------------------------------------------------
        # NAME
        # -------------------------------------------------

        user_name = get_vk_user_name(
            sender_id
        )

        if user_name:
            print(
                "User:",
                user_name,
                flush=True
            )

        # -------------------------------------------------
        # AI
        # -------------------------------------------------

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

        # -------------------------------------------------
        # MEMORY
        # -------------------------------------------------

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

        cleanup_memory()

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
