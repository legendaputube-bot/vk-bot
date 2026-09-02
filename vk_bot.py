import os
import base64
import requests
import time
import threading
from flask import Flask, request
from groq import Groq


# =========================================================
# НАСТРОЙКИ
# =========================================================

VK_TOKEN = os.environ.get("VK_TOKEN", "")
VK_CONFIRMATION_CODE = os.environ.get("VK_CONFIRMATION_CODE", "")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
VK_GROUP_SECRET = os.environ.get("VK_GROUP_SECRET", "")

VK_API_URL = "https://api.vk.com/method/messages.send"
VK_USERS_GET_URL = "https://api.vk.com/method/users.get"
VK_API_VERSION = "5.199"


# =========================================================
# МОДЕЛИ
# =========================================================

MAIN_MODEL = "openai/gpt-oss-120b"
BACKUP_MODEL = "openai/gpt-oss-20b"

VISION_MODEL = "meta-llama/llama-4-scout-17b-16e-instruct"

# Быстрое распознавание голоса
VOICE_TRANSCRIPTION_MODEL = "whisper-large-v3-turbo"

# Groq TTS
TTS_MODEL = "canopylabs/orpheus-v1-english"
TTS_VOICE = "autumn"


# =========================================================
# ЛИМИТЫ ТОКЕНОВ
# =========================================================

# Обычный текст
TEXT_MAX_TOKENS = 80

# Голосовое
VOICE_MAX_TOKENS = 140

# Изображение
IMAGE_MAX_TOKENS = 170


# =========================================================
# ПАМЯТЬ
# =========================================================

# Только 3 последних сообщения НА КАЖДОГО пользователя
MAX_HISTORY_MESSAGES = 3

users_memory = {}
memory_lock = threading.Lock()


# =========================================================
# SYSTEM PROMPT
# =========================================================

SYSTEM_PROMPT = (
    "Ты — дерзкий, языкастый бот сообщества ВКонтакте, посвящённого ИСКЛЮЧИТЕЛЬНО игре "
    "Tanks Blitz PVP битвы (разработчик EAST-GAMES LLC / Lesta Games) — мобильному танковому "
    "PVP-шутеру. Это твоё главное направление разговора.\n\n"

    "Если вопрос не связан с Tanks Blitz — можешь коротко и с юмором сказать, "
    "что бот заточен под танки.\n\n"

    "ОБРАЩЕНИЕ ПО ИМЕНИ: тебе в начале сообщения передаётся имя пользователя в формате "
    "'[Имя: ...]'. Обращайся к человеку по имени естественно. Саму пометку "
    "'[Имя: ...]' в ответе не показывай.\n\n"

    "ВАЖНО: каждый пользователь имеет отдельную историю общения. "
    "Никогда не смешивай информацию разных пользователей.\n\n"

    "НЕ ВЫДУМЫВАЙ ТОЧНЫЕ ЦИФРЫ: если не уверен в характеристиках техники, "
    "уроне, броне, калибре и других точных значениях — не придумывай их.\n\n"

    "ОТВЕЧАЙ КОРОТКО. Обычно 1-3 коротких предложения. "
    "Не пиши длинные тексты.\n\n"

    "Используй неформальный тон, лёгкую иронию и дружеские подколки. "
    "Без настоящих оскорблений и перехода на личности."
)


app = Flask(__name__)
client = Groq(api_key=GROQ_API_KEY)


# =========================================================
# ПАМЯТЬ ПОЛЬЗОВАТЕЛЯ
# =========================================================

def get_user_memory(user_id: int):

    with memory_lock:

        if user_id not in users_memory:

            users_memory[user_id] = {
                "history": [],
                "name": None,
                "lock": threading.Lock()
            }

        return users_memory[user_id]


def add_to_history(
    user_id: int,
    role: str,
    content: str
):

    user_memory = get_user_memory(user_id)

    with user_memory["lock"]:

        user_memory["history"].append({
            "role": role,
            "content": content
        })

        # Оставляем только последние 3 сообщения
        if len(user_memory["history"]) > MAX_HISTORY_MESSAGES:

            user_memory["history"] = (
                user_memory["history"][-MAX_HISTORY_MESSAGES:]
            )


def get_history(user_id: int):

    user_memory = get_user_memory(user_id)

    with user_memory["lock"]:

        return list(user_memory["history"])


# =========================================================
# ИМЯ ПОЛЬЗОВАТЕЛЯ
# =========================================================

def get_user_name(user_id: int) -> str:

    user_memory = get_user_memory(user_id)

    if user_memory["name"]:
        return user_memory["name"]

    try:

        params = {
            "access_token": VK_TOKEN,
            "v": VK_API_VERSION,
            "user_ids": user_id,
        }

        response = requests.get(
            VK_USERS_GET_URL,
            params=params,
            timeout=10
        )

        result = response.json()

        first_name = result["response"][0]["first_name"]

        user_memory["name"] = first_name

        return first_name

    except Exception as e:

        print(
            "Не удалось получить имя:",
            e,
            flush=True
        )

        return ""


# =========================================================
# МОДЕЛЬ
# =========================================================

def ask_model(
    model,
    user_id: int,
    user_message: str,
    user_name: str,
    max_tokens: int
):

    message_with_name = (
        f"[Имя: {user_name}] {user_message}"
        if user_name
        else user_message
    )

    history = get_history(user_id)

    messages = [
        {
            "role": "system",
            "content": SYSTEM_PROMPT
        }
    ]

    messages.extend(history)

    messages.append({
        "role": "user",
        "content": message_with_name
    })

    completion = client.chat.completions.create(
        model=model,
        messages=messages,
        max_tokens=max_tokens,
    )

    reply = completion.choices[0].message.content

    if not reply:

        raise RuntimeError(
            "Модель вернула пустой ответ"
        )

    return reply.strip()


# =========================================================
# GROQ
# =========================================================

MAIN_MODEL_RETRY_TIME = 60 * 60
main_model_blocked_until = 0


def ask_groq(
    user_id: int,
    user_message: str,
    user_name: str,
    max_tokens: int
) -> str:

    global main_model_blocked_until

    current_time = time.time()

    if current_time >= main_model_blocked_until:

        try:

            print(
                "Пробуем:",
                MAIN_MODEL,
                flush=True
            )

            return ask_model(
                MAIN_MODEL,
                user_id,
                user_message,
                user_name,
                max_tokens
            )

        except Exception as e:

            if is_rate_limit_error(e):

                main_model_blocked_until = (
                    time.time()
                    + MAIN_MODEL_RETRY_TIME
                )

                print(
                    "Лимит 120B достигнут. Переходим на 20B.",
                    flush=True
                )

            else:

                print(
                    "Ошибка 120B:",
                    e,
                    flush=True
                )

    print(
        "Используем:",
        BACKUP_MODEL,
        flush=True
    )

    return ask_model(
        BACKUP_MODEL,
        user_id,
        user_message,
        user_name,
        max_tokens
    )


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
    )


# =========================================================
# РАСПОЗНАВАНИЕ ГОЛОСА
# =========================================================

def transcribe_voice(audio_url: str) -> str:

    audio_response = requests.get(
        audio_url,
        timeout=15
    )

    audio_response.raise_for_status()

    transcription = client.audio.transcriptions.create(
        file=(
            "voice.ogg",
            audio_response.content
        ),
        model=VOICE_TRANSCRIPTION_MODEL,
        language="ru",
    )

    return transcription.text.strip()


# =========================================================
# GROQ TTS
# =========================================================

def generate_voice(text: str):

    # TTS-модели имеют ограничение длины.
    # Обрезаем слишком длинный ответ.
    text = text.strip()[:200]

    response = client.audio.speech.create(
        model=TTS_MODEL,
        voice=TTS_VOICE,
        input=text,
        response_format="wav"
    )

    voice_data = response.read()

    if not voice_data:

        raise RuntimeError(
            "TTS вернул пустой файл"
        )

    return voice_data


# =========================================================
# ОТПРАВКА ГОЛОСОВОГО В VK
# =========================================================

def send_vk_voice(
    peer_id: int,
    voice_data: bytes
):

    # Получаем URL загрузки голосового
    params = {
        "access_token": VK_TOKEN,
        "v": VK_API_VERSION,
        "type": "audio_message",
        "peer_id": peer_id,
    }

    upload_response = requests.get(
        "https://api.vk.com/method/docs.getMessagesUploadServer",
        params=params,
        timeout=15
    )

    upload_result = upload_response.json()

    if "error" in upload_result:

        raise RuntimeError(
            f"VK upload server error: {upload_result['error']}"
        )

    upload_url = upload_result["response"]["upload_url"]

    # Загружаем WAV
    upload = requests.post(
        upload_url,
        files={
            "file": (
                "voice.wav",
                voice_data,
                "audio/wav"
            )
        },
        timeout=30
    )

    upload_result = upload.json()

    if "error" in upload_result:

        raise RuntimeError(
            f"VK upload error: {upload_result['error']}"
        )

    # Сохраняем голосовое
    save_params = {
        "access_token": VK_TOKEN,
        "v": VK_API_VERSION,
        **upload_result
    }

    save_response = requests.post(
        "https://api.vk.com/method/docs.save",
        data=save_params,
        timeout=15
    )

    save_result = save_response.json()

    if "error" in save_result:

        raise RuntimeError(
            f"VK save error: {save_result['error']}"
        )

    doc = save_result["response"]

    owner_id = doc["owner_id"]
    doc_id = doc["id"]

    access_key = doc.get(
        "access_key"
    )

    attachment = (
        f"audio_message{owner_id}_{doc_id}"
    )

    if access_key:

        attachment += f"_{access_key}"

    params = {
        "access_token": VK_TOKEN,
        "v": VK_API_VERSION,
        "peer_id": peer_id,
        "attachment": attachment,
        "random_id": 0,
    }

    response = requests.post(
        VK_API_URL,
        data=params,
        timeout=15
    )

    result = response.json()

    if "error" in result:

        print(
            "Ошибка отправки голосового:",
            result["error"],
            flush=True
        )

    return result


# =========================================================
# ОТПРАВКА ТЕКСТА В VK
# =========================================================

def send_vk_message(
    peer_id: int,
    text: str
):

    if not text:

        text = (
            "Что-то я сейчас подвис 😅 "
            "Попробуй по позже."
        )

    params = {
        "access_token": VK_TOKEN,
        "v": VK_API_VERSION,
        "peer_id": peer_id,
        "message": text,
        "random_id": 0,
    }

    try:

        response = requests.post(
            VK_API_URL,
            data=params,
            timeout=15
        )

        result = response.json()

        if "error" in result:

            print(
                "Ошибка VK:",
                result["error"],
                flush=True
            )

        return result

    except Exception as e:

        print(
            "Ошибка отправки:",
            e,
            flush=True
        )

        return None


# =========================================================
# СОХРАНЕНИЕ
# =========================================================

def save_conversation(
    user_id: int,
    user_message: str,
    assistant_reply: str
):

    add_to_history(
        user_id,
        "user",
        user_message
    )

    add_to_history(
        user_id,
        "assistant",
        assistant_reply
    )


# =========================================================
# ОБЫЧНЫЙ ТЕКСТ
# =========================================================

def handle_message(
    peer_id: int,
    from_id: int,
    text: str
):

    try:

        user_name = get_user_name(
            from_id
        )

        reply = ask_groq(
            from_id,
            text,
            user_name,
            TEXT_MAX_TOKENS
        )

        save_conversation(
            from_id,
            text,
            reply
        )

        # Обычный текст отправляем текстом
        send_vk_message(
            peer_id,
            reply
        )

    except Exception as e:

        print(
            "Ошибка текста:",
            e,
            flush=True
        )

        send_vk_message(
            peer_id,
            "Что-то я сейчас подвис 😅 Попробуй ещё раз."
        )


# =========================================================
# ГОЛОС
# =========================================================

def handle_voice_message(
    peer_id: int,
    from_id: int,
    voice_url: str
):

    try:

        text = transcribe_voice(
            voice_url
        )

        print(
            "Распознан голос:",
            text,
            flush=True
        )

        if not text:

            return

        user_name = get_user_name(
            from_id
        )

        # Ответ модели — максимум 140 токенов
        reply = ask_groq(
            from_id,
            text,
            user_name,
            VOICE_MAX_TOKENS
        )

        save_conversation(
            from_id,
            text,
            reply
        )

        # Генерируем голосовой ответ
        voice_data = generate_voice(
            reply
        )

        # Отправляем именно голосовое
        send_vk_voice(
            peer_id,
            voice_data
        )

    except Exception as e:

        print(
            "Ошибка голосового:",
            e,
            flush=True
        )

        # Если TTS не сработал — отправляем текст,
        # чтобы пользователь всё равно получил ответ.
        try:

            send_vk_message(
                peer_id,
                "Не смог отправить голос 😅 Но я тут."
            )

        except Exception:
            pass


# =========================================================
# ИЗОБРАЖЕНИЕ
# =========================================================

def download_image_as_base64(
    image_url: str
):

    response = requests.get(
        image_url,
        timeout=20
    )

    response.raise_for_status()

    image_data = response.content

    if not image_data:

        raise RuntimeError(
            "Пустое изображение"
        )

    if len(image_data) > 20 * 1024 * 1024:

        raise RuntimeError(
            "Изображение больше 20 MB"
        )

    content_type = response.headers.get(
        "Content-Type",
        "image/jpeg"
    )

    if not content_type.startswith("image/"):

        content_type = "image/jpeg"

    encoded_image = base64.b64encode(
        image_data
    ).decode("utf-8")

    return (
        f"data:{content_type};base64,{encoded_image}"
    )


def ask_about_image(
    image_url: str,
    user_name: str,
    caption: str = ""
) -> str:

    if caption.strip():

        prompt_text = caption.strip()

    else:

        prompt_text = (
            "Посмотри на этот скриншот из Tanks Blitz. "
            "Коротко скажи, что здесь происходит."
        )

    message_with_name = (
        f"[Имя: {user_name}] {prompt_text}"
        if user_name
        else prompt_text
    )

    image_data_url = download_image_as_base64(
        image_url
    )

    completion = client.chat.completions.create(
        model=VISION_MODEL,
        messages=[
            {
                "role": "system",
                "content": SYSTEM_PROMPT
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": message_with_name
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": image_data_url
                        }
                    }
                ]
            }
        ],
        max_tokens=IMAGE_MAX_TOKENS,
    )

    reply = completion.choices[0].message.content

    if not reply:

        raise RuntimeError(
            "Vision вернула пустой ответ"
        )

    return reply.strip()


def handle_image_message(
    peer_id: int,
    from_id: int,
    image_url: str,
    caption: str
):

    try:

        user_name = get_user_name(
            from_id
        )

        reply = ask_about_image(
            image_url,
            user_name,
            caption
        )

        save_conversation(
            from_id,
            caption or "[изображение]",
            reply
        )

        # Изображения отвечаем текстом
        send_vk_message(
            peer_id,
            reply
        )

    except Exception as e:

        print(
            "Ошибка изображения:",
            e,
            flush=True
        )

        send_vk_message(
            peer_id,
            "Не смог рассмотреть скриншот 😅 Попробуй ещё раз."


        )


# =========================================================
# ФОТО VK
# =========================================================

def get_best_photo_url(photo):

    sizes = photo.get(
        "sizes",
        []
    )

    if not sizes:

        return None

    best_size = max(
        sizes,
        key=lambda size: (
            size.get("width", 0)
            * size.get("height", 0)
        )
    )

    return best_size.get("url")


# =========================================================
# CALLBACK VK
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

    except Exception as e:

        print(
            "Ошибка JSON:",
            e,
            flush=True
        )

        return "bad request", 400


    # =====================================================
    # SECRET
    # =====================================================

    if (
        VK_GROUP_SECRET
        and data.get("secret") != VK_GROUP_SECRET
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
    # НОВОЕ СООБЩЕНИЕ
    # =====================================================

    if event_type == "message_new":

        message = data.get(
            "object",
            {}
        ).get(
            "message",
            {}
        )

        peer_id = message.get(
            "peer_id"
        )

        from_id = message.get(
            "from_id"
        )

        text = message.get(
            "text",
            ""
        )

        attachments = message.get(
            "attachments",
            []
        )

        if not peer_id or not from_id:

            return "ok"


        voice_url = None
        image_url = None


        # =================================================
        # ВЛОЖЕНИЯ
        # =================================================

        for att in attachments:

            att_type = att.get(
                "type"
            )


            # ---------------------------------------------
            # ГОЛОС
            # ---------------------------------------------

            if att_type == "audio_message":

                audio_message = att.get(
                    "audio_message",
                    {}
                )

                voice_url = (
                    audio_message.get("link_ogg")
                    or
                    audio_message.get("link_mp3")
                )


            # ---------------------------------------------
            # ФОТО
            # ---------------------------------------------

            elif att_type == "photo":

                photo = att.get(
                    "photo",
                    {}
                )

                image_url = get_best_photo_url(
                    photo
                )


        # =================================================
        # ГОЛОС
        # =================================================

        if voice_url:

            threading.Thread(
                target=handle_voice_message,
                args=(
                    peer_id,
                    from_id,
                    voice_url
                ),
                daemon=True
            ).start()


        # =================================================
        # ФОТО
        # =================================================

        elif image_url:

            threading.Thread(
                target=handle_image_message,
                args=(
                    peer_id,
                    from_id,
                    image_url,
                    text
                ),
                daemon=True
            ).start()


        # =================================================
        # ОБЫЧНЫЙ ТЕКСТ
        # =================================================

        elif text.strip():

            # Больше НЕТ проверки на ?
            # Бот отвечает на любые сообщения

            threading.Thread(
                target=handle_message,
                args=(
                    peer_id,
                    from_id,
                    text
                ),
                daemon=True
            ).start()


        return "ok"


    return "ok"


# =========================================================
# ЗАПУСК
# =========================================================

if __name__ == "__main__":

    port = int(
        os.environ.get(
            "PORT",
            5000
        )
    )

    print(
        "VK AI бот запускается...",
        flush=True
    )

    print(
        f"Память: {MAX_HISTORY_MESSAGES} сообщения",
        flush=True
    )

    print(
        f"Текст: {TEXT_MAX_TOKENS} токенов",
        flush=True
    )

    print(
        f"Голос: {VOICE_MAX_TOKENS} токенов",
        flush=True
    )

    print(
        f"Изображение: {IMAGE_MAX_TOKENS} токенов",
        flush=True
    )

    app.run(
        host="0.0.0.0",
        port=port
    )
