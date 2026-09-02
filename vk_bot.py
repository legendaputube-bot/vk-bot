import os
import requests
import time
import hashlib
import re
from collections import defaultdict, deque
from flask import Flask, request
from groq import Groq


# =========================================================
# ENVIRONMENT
# =========================================================

VK_TOKEN = os.environ.get("VK_TOKEN", "")
VK_CONFIRMATION_CODE = os.environ.get("VK_CONFIRMATION_CODE", "")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
VK_GROUP_SECRET = os.environ.get("VK_GROUP_SECRET", "")
PERPLEXITY_API_KEY = os.environ.get("PERPLEXITY_API_KEY", "")

PERPLEXITY_MODEL = os.environ.get(
    "PERPLEXITY_MODEL",
    ""
)


# =========================================================
# SYSTEM PROMPT
# =========================================================

SYSTEM_PROMPT = (
    "Ты — дерзкий, языкастый бот сообщества ВКонтакте, посвящённого ИСКЛЮЧИТЕЛЬНО игре "
    "Tanks Blitz PVP битвы (разработчик EAST-GAMES LLC / Lesta Games) — мобильному танковому "
    "PVP-шутеру 7 на 7. Это твоё единственное разрешённое направление разговора.\n\n"

    "ПРАВИЛО ПО ТЕМЕ:\n"
    "Если сообщение не связано с Tanks Blitz или текущим разговором о Tanks Blitz — "
    "не отвечай по существу и с лёгким юмором напомни, что здесь говорят про танки.\n\n"

    "ГЛАВНОЕ ПРАВИЛО — НЕ ВЫДУМЫВАТЬ:\n"
    "Тебе запрещено придумывать любые конкретные игровые данные.\n"
    "Не придумывай названия танков, ветки прокачки, уровни техники, характеристики, "
    "урон, броню, пробитие, калибры, скорость, перезарядку, очки прочности, проценты, "
    "цифры, карты, режимы, события, бонус-коды или другие точные сведения.\n\n"

    "ОСОБЕННО ЗАПРЕЩЕНО:\n"
    "- составлять ветки прокачки на основе собственных знаний;\n"
    "- перечислять танки без подтверждённых данных;\n"
    "- придумывать, какой танк находится на конкретном уровне;\n"
    "- придумывать характеристики танков;\n"
    "- добавлять неподтверждённые цифры;\n"
    "- смешивать Tanks Blitz с World of Tanks PC;\n"
    "- выдавать предположение за подтверждённый факт.\n\n"

    "ЕСЛИ ЕСТЬ ДАННЫЕ ИЗ ПОИСКА:\n"
    "Используй переданную информацию как источник. "
    "Не добавляй к ней неподтверждённые сведения.\n\n"

    "ЕСЛИ ДАННЫХ НЕДОСТАТОЧНО:\n"
    "Честно скажи, что точных подтверждённых данных недостаточно. "
    "Не заполняй пробел догадкой.\n\n"

    "СКРИНШОТЫ:\n"
    "Используй только то, что действительно видно на изображении или подтверждено анализом. "
    "Не придумывай отсутствующие элементы.\n\n"

    "ПАМЯТЬ ДИАЛОГА:\n"
    "Учитывай предыдущие сообщения пользователя и свои предыдущие ответы, "
    "если они переданы в контексте. Понимай короткие продолжения вроде "
    "«а почему?», «а этот?», «а если так?», «а на 8 уровне?» в контексте предыдущего разговора.\n\n"

    "ИМЯ ПОЛЬЗОВАТЕЛЯ:\n"
    "Если в контексте указано имя пользователя, можешь иногда обращаться к нему по имени. "
    "Не вставляй имя в каждое сообщение.\n\n"

    "ФОРМАТ ОТВЕТА:\n"
    "Отвечай коротко, живо и по делу.\n"
    "Если отвечаешь списком или советами — максимум 3 пункта.\n"
    "Не пиши длинные портянки.\n\n"

    "ТОН:\n"
    "Используй неформальный тон, лёгкую иронию и подколки, "
    "но без настоящих оскорблений и переходов на личности."
)


# =========================================================
# APP
# =========================================================

app = Flask(__name__)


# =========================================================
# CLIENTS
# =========================================================

groq_client = Groq(
    api_key=GROQ_API_KEY
)


# =========================================================
# VK
# =========================================================

VK_API_URL = "https://api.vk.com/method/messages.send"
VK_API_VERSION = "5.199"


# =========================================================
# GROQ MODELS
# =========================================================

MAIN_MODEL = "openai/gpt-oss-120b"
BACKUP_MODEL = "openai/gpt-oss-20b"


# =========================================================
# 120B COOLDOWN
# =========================================================

MAIN_MODEL_RETRY_TIME = 60 * 60

main_model_blocked_until = 0


# =========================================================
# SONAR CACHE
# =========================================================

SONAR_CACHE_TIME = 30 * 60

sonar_cache = {}


# =========================================================
# USER MEMORY
# =========================================================

# Максимум последних сообщений на пользователя.
MEMORY_MESSAGES_LIMIT = 8

user_memory = defaultdict(
    lambda: deque(
        maxlen=MEMORY_MESSAGES_LIMIT
    )
)


# =========================================================
# USER NAMES
# =========================================================

# Кэш имени пользователя, чтобы не обращаться
# к VK API при каждом сообщении.
USER_NAME_CACHE_TIME = 24 * 60 * 60

user_names = {}


# =========================================================
# DUPLICATE EVENT PROTECTION
# =========================================================

PROCESSED_EVENTS_LIMIT = 1000

processed_events = {}


def already_processed(event_id):

    if not event_id:
        return False

    current_time = time.time()

    old_events = [
        event_id_key
        for event_id_key, saved_time in processed_events.items()
        if current_time - saved_time > 60 * 30
    ]

    for event_id_key in old_events:

        processed_events.pop(
            event_id_key,
            None
        )

    if event_id in processed_events:

        return True

    processed_events[event_id] = current_time

    if len(processed_events) > PROCESSED_EVENTS_LIMIT:

        oldest_key = min(
            processed_events,
            key=processed_events.get
        )

        processed_events.pop(
            oldest_key,
            None
        )

    return False


# =========================================================
# RATE LIMIT
# =========================================================

def is_rate_limit_error(error):

    error_text = str(error).lower()

    return (
        "429" in error_text
        or "rate limit" in error_text
        or "rate_limit_exceeded" in error_text
        or "tokens per day" in error_text
        or "tpd" in error_text
        or "too many requests" in error_text
    )


# =========================================================
# GREETING
# =========================================================

def is_greeting(text):

    text = text.lower().strip()

    greetings = {
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
        "добрый день",
        "добрый вечер",
        "доброе утро",
        "доброй ночи",
        "добрейший",
        "ку",
        "ку!"
    }

    return text in greetings


def greeting_response(text):

    text = text.lower().strip()

    if text == "доброе утро":
        return "Доброе утро! ☀️ Удачного дня и побольше победных боёв!"

    if text == "добрый день":
        return "Добрый день! 😎 Хорошего дня и побольше победных боёв!"

    if text == "добрый вечер":
        return "Добрый вечер! 😎 Хорошего вечера и удачных боёв!"

    if text == "доброй ночи":
        return "Доброй ночи! 🌙 Отдыхай и завтра раздавай по полной!"

    return "Привет! 👋 Хорошего дня и удачных боёв!"


# =========================================================
# LOCAL MESSAGE INTENT
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


FOLLOWUP_PHRASES = (
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
    "понял",
    "понятно",
    "не понял",
    "не понимаю",
    "объясни",
    "расскажи",
    "подробнее",
    "почему так"
)


IGNORED_MESSAGES = {
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


def looks_like_question(text):

    text_lower = text.lower().strip()

    if not text_lower:
        return False

    # Старый вариант ? теперь не обязателен,
    # но если он есть — это почти наверняка вопрос.
    if text_lower.endswith(("?", "?!", "!?")):
        return True

    words = re.findall(
        r"[а-яёa-z0-9]+",
        text_lower
    )

    if not words:
        return False

    # Начинается с вопросительного слова.
    if words[0] in QUESTION_WORDS:
        return True

    # Вопросительное слово встречается в начале короткой фразы.
    if len(words) <= 8:

        for word in words[:3]:

            if word in QUESTION_WORDS:
                return True

    # Типичные продолжения разговора.
    for phrase in FOLLOWUP_PHRASES:

        if text_lower.startswith(phrase):

            return True

    return False


def is_obvious_noise(text):

    text_lower = text.lower().strip()

    if not text_lower:
        return True

    if text_lower in IGNORED_MESSAGES:
        return True

    # Слишком короткие бессмысленные сообщения.
    if len(text_lower) <= 2:
        return True

    return False


def should_use_ai(
    text,
    user_id=None
):

    text = text.strip()

    if is_obvious_noise(text):

        return False

    # Явный вопрос.
    if looks_like_question(text):

        return True

    # Если пользователь уже недавно разговаривал
    # с ботом, короткие продолжения тоже отправляем в AI.
    if user_id:

        history = user_memory.get(
            user_id
        )

        if history:

            # Короткое сообщение после предыдущего
            # диалога почти наверняка является продолжением.
            if len(text) <= 120:

                return True

            # Фразы продолжения.
            text_lower = text.lower()

            for phrase in FOLLOWUP_PHRASES:

                if phrase in text_lower:

                    return True

    return False


# =========================================================
# VK USER INFO
# =========================================================

def get_vk_user_name(user_id):

    if not user_id:
        return None

    cached = user_names.get(
        user_id
    )

    if cached:

        saved_time, full_name = cached

        if (
            time.time() - saved_time
            < USER_NAME_CACHE_TIME
        ):

            return full_name

    try:

        response = requests.get(

            "https://api.vk.com/method/users.get",

            params={

                "access_token":
                    VK_TOKEN,

                "v":
                    VK_API_VERSION,

                "user_ids":
                    user_id,

                "fields":
                    "first_name,last_name"
            },

            timeout=10
        )

        data = response.json()

        users = data.get(
            "response",
            []
        )

        if not users:
            return None

        user = users[0]

        first_name = user.get(
            "first_name",
            ""
        ).strip()

        last_name = user.get(
            "last_name",
            ""
        ).strip()

        full_name = (
            f"{first_name} {last_name}"
        ).strip()

        if not full_name:
            return None

        user_names[user_id] = (
            time.time(),
            full_name
        )

        return full_name

    except Exception as e:

        print(
            "Ошибка получения имени VK:",
            e,
            flush=True
        )

        return None


# =========================================================
# MEMORY
# =========================================================

def add_memory(
    user_id,
    role,
    content
):

    if not user_id or not content:
        return

    user_memory[user_id].append({

        "role":
            role,

        "content":
            content
    })


def build_memory_context(
    user_id
):

    if not user_id:
        return []

    history = user_memory.get(
        user_id
    )

    if not history:
        return []

    return list(history)


def clear_old_memory():

    # Ограничиваем общее количество пользователей
    # в памяти, чтобы память Render не росла бесконечно.

    MAX_MEMORY_USERS = 5000

    if len(user_memory) <= MAX_MEMORY_USERS:
        return

    users_to_remove = (
        len(user_memory)
        - MAX_MEMORY_USERS
    )

    for _ in range(users_to_remove):

        try:

            oldest_user = next(
                iter(user_memory)
            )

            del user_memory[
                oldest_user
            ]

        except StopIteration:

            break


# =========================================================
# GROQ
# =========================================================

def ask_model(
    model,
    messages
):

    completion = (
        groq_client
        .chat
        .completions
        .create(

            model=model,

            messages=messages,

            max_tokens=500
        )
    )

    return (
        completion
        .choices[0]
        .message
        .content
        .strip()
    )


def ask_groq(
    user_message,
    user_id=None,
    user_name=None
):

    global main_model_blocked_until

    current_time = time.time()

    messages = [

        {
            "role":
                "system",

            "content":
                SYSTEM_PROMPT
        }

    ]

    # =====================================================
    # USER NAME
    # =====================================================

    if user_name:

        messages.append({

            "role":
                "system",

            "content": (
                "Имя пользователя в VK: "
                f"{user_name}. "
                "Обращайся по имени только иногда, "
                "когда это естественно."
            )
        })

    # =====================================================
    # MEMORY
    # =====================================================

    history = build_memory_context(
        user_id
    )

    if history:

        messages.append({

            "role":
                "system",

            "content": (
                "Ниже находится недавняя история "
                "диалога. Используй её для понимания "
                "контекста текущего сообщения.\n\n"
                "История диалога:"
            )
        })

        messages.extend(
            history
        )

    # =====================================================
    # CURRENT MESSAGE
    # =====================================================

    messages.append({

        "role":
            "user",

        "content":
            user_message
    })

    # =====================================================
    # 120B
    # =====================================================

    if current_time >= main_model_blocked_until:

        try:

            print(
                "Пробуем:",
                MAIN_MODEL,
                flush=True
            )

            reply = ask_model(

                MAIN_MODEL,

                messages
            )

            main_model_blocked_until = 0

            print(
                "120B успешно ответила.",
                flush=True
            )

            return reply

        except Exception as e:

            if is_rate_limit_error(e):

                main_model_blocked_until = (
                    time.time()
                    + MAIN_MODEL_RETRY_TIME
                )

                print(
                    "120B достигла лимита.",
                    flush=True
                )

                print(
                    "Переходим на 20B.",
                    flush=True
                )

            else:

                print(
                    "Ошибка 120B:",
                    e,
                    flush=True
                )

    # =====================================================
    # 20B
    # =====================================================

    try:

        print(
            "Используем:",
            BACKUP_MODEL,
            flush=True
        )

        return ask_model(

            BACKUP_MODEL,

            messages
        )

    except Exception as e:

        print(
            "Ошибка 20B:",
            e,
            flush=True
        )

        raise


# =========================================================
# WEB SEARCH ROUTER
# =========================================================

def needs_web_search(text):

    text_lower = text.lower()

    keywords = [

        "характеристик",
        "характеристика",
        "урон",
        "брон",
        "скорост",
        "точност",
        "перезаряд",
        "хп",
        "здоров",
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
    ]

    return any(
        keyword in text_lower
        for keyword in keywords
    )


# =========================================================
# SONAR CACHE KEY
# =========================================================

def sonar_cache_key(text):

    return hashlib.sha256(

        text.lower()
        .strip()
        .encode("utf-8")

    ).hexdigest()


# =========================================================
# SONAR
# =========================================================

def ask_sonar(
    user_message
):

    if not PERPLEXITY_API_KEY:

        raise RuntimeError(
            "PERPLEXITY_API_KEY не установлен."
        )

    if not PERPLEXITY_MODEL:

        raise RuntimeError(
            "PERPLEXITY_MODEL не установлен."
        )

    cache_key = sonar_cache_key(
        user_message
    )

    cached = sonar_cache.get(
        cache_key
    )

    if cached:

        saved_time, saved_answer = cached

        if (
            time.time()
            - saved_time
            < SONAR_CACHE_TIME
        ):

            print(
                "Sonar: используем кэш.",
                flush=True
            )

            return saved_answer

    print(
        "Sonar: выполняем поиск.",
        flush=True
    )

    url = (
        "https://api.perplexity.ai/"
        "chat/completions"
    )

    headers = {

        "Authorization":
            f"Bearer {PERPLEXITY_API_KEY}",

        "Content-Type":
            "application/json"
    }

    payload = {

        "model":
            PERPLEXITY_MODEL,

        "messages": [

            {

                "role":
                    "system",

                "content": (
                    "Ты поисковый помощник для "
                    "Tanks Blitz. "
                    "Найди актуальную и подтверждаемую "
                    "информацию. "
                    "Особенно внимательно отличай "
                    "Tanks Blitz от World of Tanks PC. "
                    "Не придумывай отсутствующие данные."
                )
            },

            {

                "role":
                    "user",

                "content": (
                    "Найди информацию для ответа "
                    "на следующий вопрос пользователя:\n\n"
                    + user_message
                )
            }

        ],

        "max_tokens":
            700
    }

    response = requests.post(

        url,

        headers=headers,

        json=payload,

        timeout=30
    )

    if response.status_code != 200:

        raise RuntimeError(
            f"Perplexity HTTP "
            f"{response.status_code}: "
            f"{response.text[:500]}"
        )

    data = response.json()

    try:

        answer = (
            data["choices"][0]
            ["message"]
            ["content"]
        ).strip()

    except Exception:

        raise RuntimeError(
            "Perplexity вернул неожиданный формат ответа."
        )

    if not answer:

        raise RuntimeError(
            "Perplexity вернул пустой ответ."
        )

    sonar_cache[cache_key] = (

        time.time(),

        answer
    )

    return answer


# =========================================================
# SONAR → GROQ
# =========================================================

def ask_sonar_then_groq(
    user_message,
    user_id=None,
    user_name=None
):

    sonar_answer = ask_sonar(
        user_message
    )

    prompt = (

        "Пользователь задал вопрос:\n"

        f"{user_message}\n\n"

        "Поисковый помощник нашёл следующие данные:\n"

        f"{sonar_answer}\n\n"

        "Сформируй ОДИН короткий итоговый ответ "
        "пользователю.\n\n"

        "Используй найденные данные как источник. "
        "Не добавляй собственные неподтверждённые "
        "характеристики, цифры, танки или другие факты. "
        "Не упоминай поисковый помощник, Sonar, Groq, "
        "API или внутреннюю архитектуру бота."
    )

    return ask_groq(

        prompt,

        user_id=user_id,

        user_name=user_name
    )


# =========================================================
# AI ROUTER
# =========================================================

def ask_ai(
    user_message,
    user_id=None,
    user_name=None
):

    if needs_web_search(
        user_message
    ):

        try:

            print(
                "Маршрутизация → Sonar → Groq",
                flush=True
            )

            return ask_sonar_then_groq(

                user_message,

                user_id=user_id,

                user_name=user_name
            )

        except Exception as e:

            print(
                "Sonar недоступен:",
                e,
                flush=True
            )

            print(
                "Переходим напрямую на Groq.",
                flush=True
            )

    print(
        "Маршрутизация → Groq",
        flush=True
    )

    return ask_groq(

        user_message,

        user_id=user_id,

        user_name=user_name
    )


# =========================================================
# VK SEND
# =========================================================

def send_vk_message(
    peer_id,
    text
):

    params = {

        "access_token":
            VK_TOKEN,

        "v":
            VK_API_VERSION,

        "peer_id":
            peer_id,

        "message":
            text,

        "random_id":
            0
    }

    response = requests.post(

        VK_API_URL,

        data=params,

        timeout=15
    )

    result = response.json()

    if "error" in result:

        print(
            "Ошибка VK API:",
            result["error"],
            flush=True
        )

    return result


# =========================================================
# FILE DOWNLOAD
# =========================================================

def download_file(
    url
):

    response = requests.get(

        url,

        timeout=30
    )

    response.raise_for_status()

    return response.content


# =========================================================
# VOICE TRANSCRIPTION
# =========================================================

def transcribe_voice(
    audio_url
):

    audio_data = download_file(
        audio_url
    )

    temp_file = "/tmp/vk_voice.ogg"

    with open(
        temp_file,
        "wb"
    ) as file:

        file.write(
            audio_data
        )

    with open(
        temp_file,
        "rb"
    ) as audio_file:

        transcription = (
            groq_client
            .audio
            .transcriptions
            .create(

                file=audio_file,

                model="whisper-large-v3-turbo",

                response_format="text"
            )
        )

    return str(
        transcription
    ).strip()


# =========================================================
# GET VOICE
# =========================================================

def get_voice_url(
    message
):

    attachments = message.get(
        "attachments",
        []
    )

    for attachment in attachments:

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

                "transcript":
                    transcript,

                "url":
                    None
            }

        url = audio.get(
            "link_ogg"
        )

        if url:

            return {

                "transcript":
                    None,

                "url":
                    url
            }

    return None


# =========================================================
# GET IMAGE
# =========================================================

def get_image_url(
    message
):

    attachments = message.get(
        "attachments",
        []
    )

    best_url = None

    best_size = 0

    for attachment in attachments:

        if attachment.get(
            "type"
        ) != "photo":

            continue

        photo = attachment.get(
            "photo",
            {}
        )

        sizes = photo.get(
            "sizes",
            []
        )

        for size in sizes:

            width = size.get(
                "width",
                0
            )

            height = size.get(
                "height",
                0
            )

            url = size.get(
                "url"
            )

            area = width * height

            if (
                url
                and area > best_size
            ):

                best_size = area

                best_url = url

    return best_url


# =========================================================
# IMAGE ANALYSIS
# =========================================================

def analyze_image(
    image_url,
    user_text
):

    if not PERPLEXITY_API_KEY:

        raise RuntimeError(
            "PERPLEXITY_API_KEY не установлен."
        )

    if not PERPLEXITY_MODEL:

        raise RuntimeError(
            "PERPLEXITY_MODEL не установлен."
        )

    headers = {

        "Authorization":
            f"Bearer {PERPLEXITY_API_KEY}",

        "Content-Type":
            "application/json"
    }

    user_content = [

        {

            "type":
                "text",

            "text": (
                "Проанализируй этот скриншот "
                "из Tanks Blitz.\n\n"

                "Описывай только то, что действительно "
                "видно на изображении.\n"

                "Не придумывай отсутствующие данные.\n\n"

                "Вопрос пользователя:\n"
                + user_text
            )
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

    payload = {

        "model":
            PERPLEXITY_MODEL,

        "messages": [

            {

                "role":
                    "system",

                "content": (
                    "Ты анализируешь изображения "
                    "из Tanks Blitz. "
                    "Не смешивай игру с World of Tanks PC. "
                    "Не придумывай то, чего не видно."
                )
            },

            {

                "role":
                    "user",

                "content":
                    user_content
            }

        ],

        "max_tokens":
            600
    }

    response = requests.post(

        "https://api.perplexity.ai/chat/completions",

        headers=headers,

        json=payload,

        timeout=45
    )

    if response.status_code != 200:

        raise RuntimeError(

            f"Perplexity image HTTP "
            f"{response.status_code}: "
            f"{response.text[:500]}"
        )

    data = response.json()

    analysis = (

        data["choices"][0]
        ["message"]
        ["content"]
    )

    return analysis.strip()


# =========================================================
# IMAGE → GROQ
# =========================================================

def analyze_image_then_groq(
    image_url,
    user_text,
    user_id=None,
    user_name=None
):

    image_analysis = analyze_image(

        image_url,

        user_text
    )

    prompt = (

        "Пользователь отправил скриншот "
        "из Tanks Blitz.\n\n"

        "Вопрос пользователя:\n"
        f"{user_text}\n\n"

        "Подтверждённый анализ изображения:\n"
        f"{image_analysis}\n\n"

        "Сформируй ОДИН короткий ответ "
        "пользователю.\n\n"

        "Используй только данные из анализа. "
        "Не придумывай то, чего там нет. "
        "Не упоминай внутреннюю систему, "
        "Perplexity, Sonar или Groq."
    )

    return ask_groq(

        prompt,

        user_id=user_id,

        user_name=user_name
    )


# =========================================================
# CALLBACK
# =========================================================

@app.route(
    "/callback",
    methods=["POST"]
)
def callback():

    data = request.get_json(
        force=True
    )

    # =====================================================
    # SECRET
    # =====================================================

    if (

        VK_GROUP_SECRET

        and data.get(
            "secret"
        ) != VK_GROUP_SECRET

    ):

        return "invalid secret", 403

    event_type = data.get(
        "type"
    )

    # =====================================================
    # CONFIRMATION
    # =====================================================

    if event_type == "confirmation":

        return VK_CONFIRMATION_CODE

    # =====================================================
    # ONLY MESSAGE_NEW
    # =====================================================

    if event_type != "message_new":

        return "ok"

    # =====================================================
    # DUPLICATE EVENT PROTECTION
    # =====================================================

    event_id = data.get(
        "event_id"
    )

    if already_processed(
        event_id
    ):

        print(
            "Повторное событие VK — игнорируем.",
            flush=True
        )

        return "ok"

    # =====================================================
    # MESSAGE
    # =====================================================

    message = data["object"]["message"]

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
    ) if sender_id else str(
        peer_id
    )

    text = message.get(
        "text",
        ""
    ).strip()

    # =====================================================
    # USER NAME
    # =====================================================

    user_name = get_vk_user_name(
        sender_id
    )

    if user_name:

        print(
            "Пользователь:",
            user_name,
            flush=True
        )

    # =====================================================
    # GREETING
    # =====================================================

    if is_greeting(text):

        send_vk_message(

            peer_id,

            greeting_response(
                text
            )
        )

        return "ok"

    # =====================================================
    # VOICE
    # =====================================================

    voice = get_voice_url(
        message
    )

    if voice:

        try:

            if voice["transcript"]:

                recognized_text = (
                    voice["transcript"]
                    .strip()
                )

            else:

                recognized_text = (
                    transcribe_voice(
                        voice["url"]
                    )
                )

            print(
                "Распознан голос:",
                recognized_text,
                flush=True
            )

            if not should_use_ai(

                recognized_text,

                user_id
            ):

                print(
                    "Голос не требует ответа — "
                    "игнорируем.",
                    flush=True
                )

                return "ok"

            # Добавляем голос пользователя
            # в память только перед AI.
            add_memory(

                user_id,

                "user",

                recognized_text
            )

            reply = ask_ai(

                recognized_text,

                user_id=user_id,

                user_name=user_name
            )

            add_memory(

                user_id,

                "assistant",

                reply
            )

            clear_old_memory()

            send_vk_message(

                peer_id,

                reply
            )

        except Exception as e:

            print(
                "Ошибка обработки голоса:",
                e,
                flush=True
            )

        return "ok"

    # =====================================================
    # IMAGE
    # =====================================================

    image_url = get_image_url(
        message
    )

    if image_url:

        # Для изображения больше не нужен знак ?.
        # Если есть осмысленный текст — анализируем.
        # Если текста нет, изображение игнорируем.

        if not text:

            print(
                "Скриншот без текста — игнорируем.",
                flush=True
            )

            return "ok"

        if not should_use_ai(

            text,

            user_id
        ):

            print(
                "Текст к скриншоту не требует "
                "ответа — игнорируем.",
                flush=True
            )

            return "ok"

        try:

            add_memory(

                user_id,

                "user",

                text
            )

            reply = analyze_image_then_groq(

                image_url,

                text,

                user_id=user_id,

                user_name=user_name
            )

            add_memory(

                user_id,

                "assistant",

                reply
            )

            clear_old_memory()

            send_vk_message(

                peer_id,

                reply
            )

        except Exception as e:

            print(
                "Ошибка анализа изображения:",
                e,
                flush=True
            )

        return "ok"

    # =====================================================
    # EMPTY TEXT
    # =====================================================

    if not text:

        return "ok"

    # =====================================================
    # SMART LOCAL ROUTER
    # =====================================================

    if not should_use_ai(

        text,

        user_id
    ):

        print(
            "Сообщение не требует AI — "
            "игнорируем без расхода токенов.",
            flush=True
        )

        return "ok"

    # =====================================================
    # AI
    # =====================================================

    try:

        # Сначала сохраняем сообщение пользователя.
        add_memory(

            user_id,

            "user",

            text
        )

        reply = ask_ai(

            text,

            user_id=user_id,

            user_name=user_name
        )

        # Затем сохраняем ответ бота.
        add_memory(

            user_id,

            "assistant",

            reply
        )

        clear_old_memory()

    except Exception as e:

        print(
            "Ошибка AI:",
            e,
            flush=True
        )

        reply = (
            "Танковый мозг сейчас немного "
            "заглох 😅 Попробуй ещё раз."
        )

    # =====================================================
    # ONE FINAL MESSAGE
    # =====================================================

    send_vk_message(

        peer_id,

        reply
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
