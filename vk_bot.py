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


# =========================================================
# SYSTEM PROMPT
# =========================================================

SYSTEM_PROMPT = (
    "Ты — дерзкий, языкастый бот сообщества ВКонтакте, посвящённого ИСКЛЮЧИТЕЛЬНО игре "
    "Tanks Blitz PVP битвы (разработчик EAST-GAMES LLC / Lesta Games) — мобильному танковому "
    "PVP-шутеру 7 на 7. Это твоё единственное разрешённое направление разговора.\n\n"

    "Отвечай коротко, живо, по делу. Используй неформальный тон, лёгкую иронию и подколки, "
    "но без грубости и оскорблений.\n\n"

    "СТРОГОЕ ПРАВИЛО:\n"
    "Если тебе переданы найденные данные из интернета, используй их для ответа, "
    "но не придумывай характеристики танков, числа, проценты и другие точные данные.\n\n"

    "Если данных недостаточно — честно скажи об этом. "
    "Не выдавай догадки за факты.\n\n"

    "Если пользователь прислал скриншот, анализируй только то, что действительно видно "
    "на изображении. Не придумывай то, чего на скриншоте нет."
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
# MODELS
# =========================================================

MAIN_MODEL = "openai/gpt-oss-120b"
BACKUP_MODEL = "openai/gpt-oss-20b"

# Perplexity модель для актуальной информации
PERPLEXITY_MODEL = "sonar-pro"


# =========================================================
# 120B COOLDOWN
# =========================================================

MAIN_MODEL_RETRY_TIME = 60 * 60

main_model_blocked_until = 0


# =========================================================
# SONAR CACHE
# =========================================================

# Храним результаты поиска некоторое время,
# чтобы одинаковые вопросы не отправлять в интернет снова.

SONAR_CACHE_TIME = 30 * 60

sonar_cache = {}


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
# DETECT HELLO
# =========================================================

def is_greeting(text):
    text = text.lower().strip()

    greetings = [
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
        "ку!",
    ]

    return text in greetings


# =========================================================
# FREE GREETING
# =========================================================

def greeting_response(text):
    text = text.lower().strip()

    if "доброе утро" in text:
        return "Доброе утро! ☀️ Удачного дня и побольше победных боёв!"

    if "добрый день" in text:
        return "Добрый день! 😎 Хорошего дня и побольше победных боёв!"

    if "добрый вечер" in text:
        return "Добрый вечер! 😎 Хорошего вечера и удачных боёв!"

    if "доброй ночи" in text:
        return "Доброй ночи! 🌙 Отдыхай и завтра раздавай по полной!"

    return "Привет! 👋 Хорошего дня и удачных боёв!"


# =========================================================
# CHECK QUESTION
# =========================================================

def is_question(text):
    """
    ИИ запускается только если вопрос заканчивается на ?
    """

    return text.strip().endswith("?")


# =========================================================
# BASIC TEXT MODEL
# =========================================================

def ask_model(model, user_message):
    completion = groq_client.chat.completions.create(
        model=model,

        messages=[
            {
                "role": "system",
                "content": SYSTEM_PROMPT
            },
            {
                "role": "user",
                "content": user_message
            }
        ],

        max_tokens=500
    )

    return completion.choices[0].message.content


# =========================================================
# 120B → 20B
# =========================================================

def ask_groq(user_message):
    global main_model_blocked_until

    current_time = time.time()

    # -----------------------------------------------------
    # 120B
    # -----------------------------------------------------

    if current_time >= main_model_blocked_until:

        try:
            print(
                "Пробуем:",
                MAIN_MODEL,
                flush=True
            )

            reply = ask_model(
                MAIN_MODEL,
                user_message
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


    # -----------------------------------------------------
    # 20B
    # -----------------------------------------------------

    try:

        print(
            "Используем:",
            BACKUP_MODEL,
            flush=True
        )

        return ask_model(
            BACKUP_MODEL,
            user_message
        )

    except Exception as e:

        print(
            "Ошибка 20B:",
            e,
            flush=True
        )

        raise


# =========================================================
# DETECT WHETHER WEB SEARCH IS NEEDED
# =========================================================

def needs_web_search(text):
    """
    Простая экономная маршрутизация.

    Не вызываем Sonar на каждый вопрос.
    """

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
        "бонус-код"
    ]

    return any(
        keyword in text_lower
        for keyword in keywords
    )


# =========================================================
# PERPLEXITY / SONAR
# =========================================================

def sonar_cache_key(text):

    return hashlib.sha256(
        text.lower().strip().encode("utf-8")
    ).hexdigest()


def ask_sonar(user_message):

    if not PERPLEXITY_API_KEY:

        raise RuntimeError(
            "PERPLEXITY_API_KEY не установлен."
        )

    cache_key = sonar_cache_key(
        user_message
    )

    # -----------------------------------------------------
    # CACHE
    # -----------------------------------------------------

    cached = sonar_cache.get(cache_key)

    if cached:

        saved_time, saved_answer = cached

        if time.time() - saved_time < SONAR_CACHE_TIME:

            print(
                "Sonar: используем кэш.",
                flush=True
            )

            return saved_answer


    print(
        "Sonar: выполняем веб-поиск.",
        flush=True
    )

    url = "https://api.perplexity.ai/chat/completions"

    headers = {
        "Authorization": f"Bearer {PERPLEXITY_API_KEY}",
        "Content-Type": "application/json"
    }

    payload = {

        "model": PERPLEXITY_MODEL,

        "messages": [

            {
                "role": "system",
                "content": (
                    "Ты информационный помощник по игре Tanks Blitz. "
                    "Ищи актуальную информацию в интернете. "
                    "Особенно внимательно отличай Tanks Blitz от World of Tanks PC. "
                    "Не смешивай характеристики разных игр."
                )
            },

            {
                "role": "user",
                "content": (
                    "Найди актуальную и проверяемую информацию "
                    "по Tanks Blitz для следующего вопроса:\n\n"
                    + user_message
                )
            }

        ],

        "max_tokens": 700
    }

    response = requests.post(
        url,
        headers=headers,
        json=payload,
        timeout=30
    )

    if response.status_code != 200:

        raise RuntimeError(
            f"Perplexity HTTP {response.status_code}: "
            f"{response.text[:500]}"
        )

    data = response.json()

    try:

        answer = data["choices"][0]["message"]["content"]

    except Exception:

        raise RuntimeError(
            "Perplexity вернул неожиданный формат ответа."
        )

    # -----------------------------------------------------
    # SAVE CACHE
    # -----------------------------------------------------

    sonar_cache[cache_key] = (
        time.time(),
        answer
    )

    return answer


# =========================================================
# SONAR → 120B
# =========================================================

def ask_sonar_then_groq(user_message):

    sonar_answer = ask_sonar(
        user_message
    )

    prompt = (
        "Пользователь задал вопрос:\n"
        f"{user_message}\n\n"

        "Ниже приведены данные, найденные через веб-поиск:\n"
        f"{sonar_answer}\n\n"

        "Используй найденную информацию как источник. "
        "Сформируй короткий, понятный ответ пользователю. "
        "Не придумывай дополнительные характеристики или числа."
    )

    return ask_groq(
        prompt
    )


# =========================================================
# SMART ROUTER
# =========================================================

def ask_ai(user_message):

    # -----------------------------------------------------
    # АКТУАЛЬНАЯ ИНФОРМАЦИЯ
    # -----------------------------------------------------

    if needs_web_search(user_message):

        try:

            print(
                "Маршрутизация → Sonar",
                flush=True
            )

            return ask_sonar_then_groq(
                user_message
            )

        except Exception as e:

            print(
                "Ошибка Sonar:",
                e,
                flush=True
            )

            # Если Sonar не сработал,
            # всё равно пытаемся ответить через Groq.

            print(
                "Sonar недоступен → Groq",
                flush=True
            )

    # -----------------------------------------------------
    # ОБЫЧНЫЙ ВОПРОС
    # -----------------------------------------------------

    print(
        "Маршрутизация → Groq",
        flush=True
    )

    return ask_groq(
        user_message
    )


# =========================================================
# VK SEND MESSAGE
# =========================================================

def send_vk_message(peer_id, text):

    params = {
        "access_token": VK_TOKEN,
        "v": VK_API_VERSION,
        "peer_id": peer_id,
        "message": text,
        "random_id": 0
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
# DOWNLOAD FILE
# =========================================================

def download_file(url):

    response = requests.get(
        url,
        timeout=30
    )

    response.raise_for_status()

    return response.content


# =========================================================
# VOICE RECOGNITION
# =========================================================

def transcribe_voice(audio_url):

    if not GROQ_API_KEY:
        raise RuntimeError(
            "GROQ_API_KEY не установлен."
        )

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

        transcription = groq_client.audio.transcriptions.create(

            file=audio_file,

            model="whisper-large-v3-turbo",

            response_format="text"
        )

    return str(
        transcription
    ).strip()


# =========================================================
# GET VOICE URL
# =========================================================

def get_voice_url(message):

    # VK иногда передаёт готовую расшифровку.
    # Если она есть — не тратим дополнительный запрос.

    attachments = message.get(
        "attachments",
        []
    )

    for attachment in attachments:

        if attachment.get("type") != "audio_message":
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
                "transcript": transcript,
                "url": None
            }

        url = audio.get(
            "link_ogg"
        )

        if url:

            return {
                "transcript": None,
                "url": url
            }

    return None


# =========================================================
# GET IMAGE URL
# =========================================================

def get_image_url(message):

    attachments = message.get(
        "attachments",
        []
    )

    best_url = None
    best_size = 0

    for attachment in attachments:

        if attachment.get("type") != "photo":
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

            if url and area > best_size:

                best_size = area
                best_url = url

    return best_url


# =========================================================
# IMAGE ANALYSIS
# =========================================================

def analyze_image(image_url, user_text):

    if not PERPLEXITY_API_KEY:

        raise RuntimeError(
            "PERPLEXITY_API_KEY не установлен."
        )

    headers = {
        "Authorization": f"Bearer {PERPLEXITY_API_KEY}",
        "Content-Type": "application/json"
    }

    user_content = [

        {
            "type": "text",
            "text": (
                "Проанализируй этот скриншот из Tanks Blitz. "
                "Опиши только то, что реально видно на изображении. "
                "Если видно название танка, карту, счёт, "
                "позицию или другие игровые элементы — укажи их. "
                "Не придумывай отсутствующие данные."
                "\n\nКомментарий пользователя: "
                + (user_text or "Проанализируй скриншот.")
            )
        },

        {
            "type": "image_url",
            "image_url": {
                "url": image_url
            }
        }

    ]

    payload = {

        "model": PERPLEXITY_MODEL,

        "messages": [

            {
                "role": "system",
                "content": (
                    "Ты анализируешь изображения из Tanks Blitz. "
                    "Отличай Tanks Blitz от World of Tanks PC."
                )
            },

            {
                "role": "user",
                "content": user_content
            }

        ],

        "max_tokens": 600
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

    analysis = data["choices"][0]["message"]["content"]

    return analysis


# =========================================================
# IMAGE → 120B
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
        "Пользователь отправил скриншот из Tanks Blitz.\n\n"

        "Анализ изображения:\n"
        f"{image_analysis}\n\n"

        "Сообщение пользователя:\n"
        f"{user_text}\n\n"

        "На основе анализа дай короткий и полезный ответ. "
        "Не придумывай то, чего нет в анализе изображения."
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

    # -----------------------------------------------------
    # SECRET
    # -----------------------------------------------------

    if (
        VK_GROUP_SECRET
        and data.get("secret") != VK_GROUP_SECRET
    ):

        return "invalid secret", 403


    event_type = data.get(
        "type"
    )


    # -----------------------------------------------------
    # CONFIRMATION
    # -----------------------------------------------------

    if event_type == "confirmation":

        return VK_CONFIRMATION_CODE


    # -----------------------------------------------------
    # MESSAGE
    # -----------------------------------------------------

    if event_type == "message_new":

        message = data["object"]["message"]

        user_id = message["from_id"]

        peer_id = message["peer_id"]

        text = message.get(
            "text",
            ""
        ).strip()


        # =================================================
        # ПРИВЕТСТВИЕ — 0 ТОКЕНОВ
        # =================================================

        if is_greeting(text):

            send_vk_message(
                peer_id,
                greeting_response(text)
            )

            return "ok"


        # =================================================
        # ГОЛОС
        # =================================================

        voice = get_voice_url(
            message
        )

        if voice:

            try:

                # Если VK уже дал текст —
                # используем его бесплатно.

                if voice["transcript"]:

                    recognized_text = voice["transcript"]

                else:

                    recognized_text = transcribe_voice(
                        voice["url"]
                    )

                print(
                    "Распознан голос:",
                    recognized_text,
                    flush=True
                )

                # Голос считается вопросом,
                # если распознанный текст заканчивается ?

                if not is_question(
                    recognized_text
                ):

                    return "ok"

                reply = ask_ai(
                    recognized_text
                )

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


        # =================================================
        # СКРИНШОТ
        # =================================================

        image_url = get_image_url(
            message
        )

        if image_url:

            # Чтобы анализ изображения тоже не запускался
            # на любой случайный скриншот,
            # требуем ? в текстовом сообщении.

            if not is_question(text):

                return "ok"

            try:

                reply = analyze_image_then_groq(
                    image_url,
                    text
                )

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


        # =================================================
        # ОБЫЧНЫЙ ТЕКСТ
        # =================================================

        if not text:

            return "ok"


        # =================================================
        # НЕТ ? В КОНЦЕ — МОЛЧИМ
        # =================================================

        if not is_question(text):

            print(
                "Сообщение без ? — игнорируем.",
                flush=True
            )

            return "ok"


        # =================================================
        # AI
        # =================================================

        try:

            reply = ask_ai(
                text
            )

        except Exception as e:

            reply = (
                "Эх, танковый мозг сейчас "
                "немного заглох 😅 "
                "Попробуй ещё раз."
            )

            print(
                "Ошибка AI:",
                e,
                flush=True
            )


        # =================================================
        # SEND
        # =================================================

        send_vk_message(
            peer_id,
            reply
        )


        return "ok"


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
