import os
import requests
import time
import hashlib
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

# Модель Perplexity указываем через Render Environment Variable.
# НЕ придумываем название модели прямо в коде.
PERPLEXITY_MODEL = os.environ.get("PERPLEXITY_MODEL", "")


# =========================================================
# SYSTEM PROMPT
# =========================================================

SYSTEM_PROMPT = (
    "Ты — дерзкий, языкастый бот сообщества ВКонтакте, посвящённого ИСКЛЮЧИТЕЛЬНО игре "
    "Tanks Blitz PVP битвы (разработчик EAST-GAMES LLC / Lesta Games) — мобильному танковому "
    "PVP-шутеру 7 на 7. Это твоё единственное разрешённое направление разговора.\n\n"

    "СТРОГОЕ ПРАВИЛО ПО ТЕМЕ:\n"
    "Если вопрос не связан с Tanks Blitz — дерзко и с юмором отказывайся отвечать "
    "по существу и напоминай, что здесь говорят только про танки.\n\n"

    "ГЛАВНОЕ ПРАВИЛО — НЕ ВЫДУМЫВАТЬ:\n"
    "Тебе запрещено придумывать любые конкретные игровые данные.\n"
    "Не придумывай названия танков, ветки прокачки, уровни техники, характеристики, "
    "урон, броню, пробитие, калибры, скорость, перезарядку, очки прочности, проценты, "
    "цифры, карты, режимы, события, бонус-коды или другие точные сведения.\n\n"

    "ОСОБЕННО ЗАПРЕЩЕНО:\n"
    "- составлять ветки прокачки от одного уровня до другого на основе собственных знаний;\n"
    "- перечислять танки, если подтверждённых данных о них нет;\n"
    "- придумывать, какой конкретно танк находится на конкретном уровне;\n"
    "- придумывать характеристики танков;\n"
    "- добавлять к найденной информации собственные неподтверждённые цифры;\n"
    "- смешивать Tanks Blitz с World of Tanks PC;\n"
    "- выдавать предположение за подтверждённый факт.\n\n"

    "ЕСЛИ ЕСТЬ ДАННЫЕ ИЗ ПОИСКА:\n"
    "Если тебе передана информация, найденная поисковым помощником, используй её "
    "для ответа. Не добавляй к ней неподтверждённые сведения.\n\n"

    "ЕСЛИ ДАННЫХ НЕДОСТАТОЧНО:\n"
    "Честно скажи, что точных подтверждённых данных недостаточно. "
    "Не пытайся заполнить пробел догадкой.\n\n"

    "СКРИНШОТЫ:\n"
    "Если тебе передан анализ изображения, используй только то, что действительно "
    "видно или было подтверждённо в анализе. Не придумывай отсутствующие элементы.\n\n"

    "ФОРМАТ ОТВЕТА:\n"
    "Отвечай коротко, живо и по делу.\n"
    "Если отвечаешь списком или советами — максимум 3 пункта.\n"
    "Не пиши длинные портянки.\n\n"

    "ТОН:\n"
    "Используй неформальный тон, лёгкую иронию и подколки, но без настоящих "
    "оскорблений и переходов на личности."
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
# DUPLICATE EVENT PROTECTION
# =========================================================

# VK Callback иногда может повторно доставить одно и то же событие.
# Поэтому запоминаем обработанные event_id.

PROCESSED_EVENTS_LIMIT = 1000

processed_events = {}


def already_processed(event_id):

    if not event_id:
        return False

    current_time = time.time()

    # Удаляем старые события
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

    # Защита от бесконечного роста
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
# QUESTION CHECK
# =========================================================

def is_question(text):

    return text.strip().endswith("?")


# =========================================================
# GROQ
# =========================================================

def ask_model(model, messages):

    completion = groq_client.chat.completions.create(

        model=model,

        messages=messages,

        max_tokens=500
    )

    return completion.choices[0].message.content.strip()


def ask_groq(user_message):

    global main_model_blocked_until

    current_time = time.time()


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

                [
                    {
                        "role": "system",
                        "content": SYSTEM_PROMPT
                    },
                    {
                        "role": "user",
                        "content": user_message
                    }
                ]
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

            [
                {
                    "role": "system",
                    "content": SYSTEM_PROMPT
                },
                {
                    "role": "user",
                    "content": user_message
                }
            ]
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
        text.lower().strip().encode("utf-8")
    ).hexdigest()


# =========================================================
# SONAR
# =========================================================

def ask_sonar(user_message):

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


    # =====================================================
    # CACHE
    # =====================================================

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


    # =====================================================
    # SEARCH
    # =====================================================

    print(
        "Sonar: выполняем поиск.",
        flush=True
    )


    url = "https://api.perplexity.ai/chat/completions"


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
                "role": "system",

                "content": (
                    "Ты поисковый помощник для Tanks Blitz. "
                    "Найди актуальную и подтверждаемую информацию. "
                    "Особенно внимательно отличай Tanks Blitz "
                    "от World of Tanks PC. "
                    "Не придумывай отсутствующие данные."
                )
            },

            {
                "role": "user",

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


    # =====================================================
    # CACHE
    # =====================================================

    sonar_cache[cache_key] = (

        time.time(),

        answer
    )


    return answer


# =========================================================
# SONAR → GROQ
# =========================================================

def ask_sonar_then_groq(user_message):

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
        prompt
    )


# =========================================================
# AI ROUTER
# =========================================================

def ask_ai(user_message):

    # =====================================================
    # АКТУАЛЬНЫЕ / КОНКРЕТНЫЕ ДАННЫЕ
    # =====================================================

    if needs_web_search(user_message):

        try:

            print(
                "Маршрутизация → Sonar → Groq",
                flush=True
            )

            return ask_sonar_then_groq(
                user_message
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


    # =====================================================
    # ОБЫЧНЫЙ ВОПРОС
    # =====================================================

    print(
        "Маршрутизация → Groq",
        flush=True
    )

    return ask_groq(
        user_message
    )


# =========================================================
# VK SEND
# =========================================================

def send_vk_message(peer_id, text):

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

def download_file(url):

    response = requests.get(

        url,

        timeout=30
    )

    response.raise_for_status()

    return response.content


# =========================================================
# VOICE TRANSCRIPTION
# =========================================================

def transcribe_voice(audio_url):

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

def get_voice_url(message):

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

def get_image_url(message):

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
    user_text
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
        prompt
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


    text = message.get(
        "text",
        ""
    ).strip()


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


            # Голос запускает ИИ только если
            # распознанный текст заканчивается ?

            if not is_question(
                recognized_text
            ):

                print(
                    "Голос без ? — игнорируем.",
                    flush=True
                )

                return "ok"


            reply = ask_ai(
                recognized_text
            )


            # ОДНА отправка

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

        # Скриншот обрабатываем только,
        # если пользователь добавил вопрос.

        if not is_question(text):

            print(
                "Скриншот без ? — игнорируем.",
                flush=True
            )

            return "ok"


        try:

            reply = analyze_image_then_groq(

                image_url,

                text
            )


            # ОДНА отправка

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
    # QUESTION ONLY
    # =====================================================

    if not is_question(text):

        print(
            "Сообщение без ? — игнорируем.",
            flush=True
        )

        return "ok"


    # =====================================================
    # AI
    # =====================================================

    try:

        reply = ask_ai(
            text
        )


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
