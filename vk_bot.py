import os
import base64
import requests
import time
import threading
from flask import Flask, request
from groq import Groq


VK_TOKEN = os.environ.get("VK_TOKEN", "")
VK_CONFIRMATION_CODE = os.environ.get("VK_CONFIRMATION_CODE", "")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
VK_GROUP_SECRET = os.environ.get("VK_GROUP_SECRET", "")


SYSTEM_PROMPT = (
    "Ты — дерзкий, языкастый бот сообщества ВКонтакте, посвящённого ИСКЛЮЧИТЕЛЬНО игре "
    "Tanks Blitz PVP битвы (разработчик EAST-GAMES LLC / Lesta Games) — мобильному танковому "
    "PVP-шутеру. Это твоё единственное разрешённое направление разговора. "
    "Если вопрос не связан с этой игрой — дерзко и с юмором отказывайся отвечать по существу, "
    "напоминай, что тут говорят только про танки.\n\n"

    "ОБРАЩЕНИЕ ПО ИМЕНИ: тебе в начале сообщения передаётся имя пользователя в формате "
    "'[Имя: ...]'. Обращайся к человеку по этому имени, естественно вписывая его в ответ. "
    "Саму пометку '[Имя: ...]' в ответе не показывай.\n\n"

    "ВАЖНО: каждый пользователь имеет отдельную историю общения. "
    "Никогда не смешивай информацию, вопросы или контекст разных пользователей.\n\n"

    "ЗАПРЕТ НА ВЫДУМЫВАНИЕ ТОЧНЫХ ЦИФР: не придумывай точные характеристики техники, "
    "калибры, урон, броню, названия валюты и другие конкретные цифры — ты их не знаешь. "
    "Если спрашивают про конкретные характеристики техники или что качать — отвечай в общих "
    "чертах и советуй посмотреть актуальные гайды и обзоры техники на YouTube, там всё "
    "наглядно показывают с цифрами и геймплеем.\n\n"

    "ФОРМАТ ОТВЕТА: отвечай КОРОТКО, максимум 2-3 предложения или максимум 3 пункта списком. "
    "Никаких длинных портянок текста.\n\n"

    "Используешь неформальный тон, лёгкую иронию и подколки, но без грубости и оскорблений. "
    "Не хами по-настоящему и не переходи на личности — дерзость должна быть смешной, "
    "а не обидной."
)


app = Flask(__name__)
client = Groq(api_key=GROQ_API_KEY)

VK_API_URL = "https://api.vk.com/method/messages.send"
VK_USERS_GET_URL = "https://api.vk.com/method/users.get"
VK_API_VERSION = "5.199"


MAIN_MODEL = "openai/gpt-oss-120b"
BACKUP_MODEL = "openai/gpt-oss-20b"

# Модель для обработки изображений
VISION_MODEL = "meta-llama/llama-4-scout-17b-16e-instruct"

MAIN_MODEL_RETRY_TIME = 60 * 60
main_model_blocked_until = 0


# =========================================================
# ПАМЯТЬ ПОЛЬЗОВАТЕЛЕЙ
# =========================================================

# Максимум 25 сообщений НА КАЖДОГО пользователя
MAX_HISTORY_MESSAGES = 25

users_memory = {}
memory_lock = threading.Lock()


def get_user_memory(user_id: int):
    """
    Получает отдельную память конкретного пользователя.
    У каждого user_id своя история.
    """

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

        # Оставляем только последние 25 сообщений
        if len(user_memory["history"]) > MAX_HISTORY_MESSAGES:

            user_memory["history"] = (
                user_memory["history"][-MAX_HISTORY_MESSAGES:]
            )


def get_history(user_id: int):

    user_memory = get_user_memory(user_id)

    with user_memory["lock"]:

        return list(user_memory["history"])


# =========================================================
# ПРОВЕРКА: ПРИВЕТСТВИЕ
# =========================================================

def is_greeting(text: str) -> bool:

    text = text.strip().lower()

    greetings = (
        "привет",
        "прив",
        "здарова",
        "здорово",
        "здравствуй",
        "здравствуйте",
        "доброе утро",
        "добрый день",
        "добрый вечер",
        "хай",
        "хелло",
        "hello",
        "ку",
        "салют"
    )

    # Убираем простую пунктуацию в конце
    cleaned = text.rstrip("!., ")

    return cleaned in greetings


# =========================================================
# ПРОВЕРКА: НУЖНО ЛИ ОТВЕЧАТЬ
# =========================================================

def should_answer(text: str) -> bool:

    if not text:
        return False

    text = text.strip()

    # Приветствия — отвечаем без вопросительного знака
    if is_greeting(text):
        return True

    # Всё остальное — только если есть ?
    if "?" in text:
        return True

    # Нет ? — игнорируем
    return False


# =========================================================
# ИМЯ ПОЛЬЗОВАТЕЛЯ
# =========================================================

def get_user_name(user_id: int) -> str:

    user_memory = get_user_memory(user_id)

    # Если имя уже получали — повторно VK не дёргаем
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
            "Не удалось получить имя пользователя:",
            e,
            flush=True
        )

        return ""


# =========================================================
# ОБЫЧНАЯ МОДЕЛЬ
# =========================================================

def ask_model(
    model,
    user_id: int,
    user_message: str,
    user_name: str
):

    message_with_name = (
        f"[Имя: {user_name}] {user_message}"
        if user_name
        else user_message
    )

    # Получаем ИМЕННО историю этого пользователя
    history = get_history(user_id)

    messages = [
        {
            "role": "system",
            "content": SYSTEM_PROMPT
        }
    ]

    # Добавляем прошлую историю конкретного пользователя
    messages.extend(history)

    # Добавляем текущее сообщение
    messages.append({
        "role": "user",
        "content": message_with_name
    })

    completion = client.chat.completions.create(
        model=model,
        messages=messages,
        max_tokens=300,
    )

    reply = completion.choices[0].message.content

    if not reply:
        raise RuntimeError(
            "Модель вернула пустой ответ"
        )

    return reply.strip()


# =========================================================
# GROQ — ОСНОВНАЯ / ЗАПАСНАЯ МОДЕЛЬ
# =========================================================

def ask_groq(
    user_id: int,
    user_message: str,
    user_name: str
) -> str:

    global main_model_blocked_until

    current_time = time.time()

    if current_time >= main_model_blocked_until:

        try:

            print(
                "Пробуем основную модель:",
                MAIN_MODEL,
                flush=True
            )

            reply = ask_model(
                MAIN_MODEL,
                user_id,
                user_message,
                user_name
            )

            main_model_blocked_until = 0

            print(
                "120B работает. Используем основную модель.",
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
                    "Лимит 120B достигнут. "
                    "Переходим на 20B.",
                    flush=True
                )

            else:

                print(
                    "Ошибка 120B:",
                    e,
                    flush=True
                )

                print(
                    "Временно используем 20B.",
                    flush=True
                )

    print(
        "Используем запасную модель:",
        BACKUP_MODEL,
        flush=True
    )

    return ask_model(
        BACKUP_MODEL,
        user_id,
        user_message,
        user_name
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
# ГОЛОСОВЫЕ
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
        model="whisper-large-v3",
        language="ru",
    )

    return transcription.text.strip()


# =========================================================
# СКАЧИВАНИЕ ИЗОБРАЖЕНИЯ VK
# =========================================================

def download_image_as_base64(image_url: str):

    print(
        "Скачиваем изображение из VK...",
        flush=True
    )

    response = requests.get(
        image_url,
        timeout=20
    )

    response.raise_for_status()

    image_data = response.content

    if not image_data:
        raise RuntimeError(
            "VK вернул пустое изображение"
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

    data_url = (
        f"data:{content_type};base64,{encoded_image}"
    )

    print(
        "Изображение успешно загружено:",
        round(len(image_data) / 1024, 1),
        "KB",
        flush=True
    )

    return data_url


# =========================================================
# АНАЛИЗ ИЗОБРАЖЕНИЯ
# =========================================================

def ask_about_image(
    image_url: str,
    user_name: str,
    caption: str = ""
) -> str:

    if caption and caption.strip():

        prompt_text = caption.strip()

    else:

        prompt_text = (
            "Посмотри на этот скриншот из Tanks Blitz. "
            "Коротко прокомментируй, что на нём происходит, "
            "в своём дерзком и дружеском стиле."
        )

    message_with_name = (
        f"[Имя: {user_name}] {prompt_text}"
        if user_name
        else prompt_text
    )

    image_data_url = download_image_as_base64(
        image_url
    )

    print(
        "Отправляем изображение в:",
        VISION_MODEL,
        flush=True
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
        max_tokens=300,
    )

    reply = completion.choices[0].message.content

    if not reply:
        raise RuntimeError(
            "Vision-модель вернула пустой ответ"
        )

    return reply.strip()


# =========================================================
# ОТПРАВКА В VK
# =========================================================

def send_vk_message(
    peer_id: int,
    text: str
):

    if not text:

        text = (
            "Что-то я сейчас подвис 😅 "
            "Попробуй ещё раз."
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
                "Ошибка VK API:",
                result["error"],
                flush=True
            )

        return result

    except Exception as e:

        print(
            "Ошибка отправки VK:",
            e,
            flush=True
        )

        return None


# =========================================================
# СОХРАНЕНИЕ СООБЩЕНИЙ
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
# ТЕКСТ
# =========================================================

def handle_message(
    peer_id: int,
    from_id: int,
    text: str
):

    try:

        # Дополнительная защита
        if not should_answer(text):

            print(
                "Сообщение проигнорировано:",
                text,
                flush=True
            )

            return

        user_name = get_user_name(
            from_id
        )

        reply = ask_groq(
            from_id,
            text,
            user_name
        )

        # Сохраняем только после успешного ответа
        save_conversation(
            from_id,
            text,
            reply
        )

    except Exception as e:

        reply = (
            "Что-то я сейчас подвис 😅 "
            "Попробуй написать ещё раз."
        )

        print(
            "Ошибка при обращении к Groq:",
            e,
            flush=True
        )

    send_vk_message(
        peer_id,
        reply
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

            print(
                "Голос пустой — игнорируем.",
                flush=True
            )

            return

        # После распознавания проверяем:
        # приветствие ИЛИ вопрос с ?
        if not should_answer(text):

            print(
                "Голос не является вопросом/приветствием — игнорируем.",
                flush=True
            )

            return

        user_name = get_user_name(
            from_id
        )

        reply = ask_groq(
            from_id,
            text,
            user_name
        )

        save_conversation(
            from_id,
            text,
            reply
        )

    except Exception as e:

        reply = (
            "Не смог разобрать голосовое 😅 "
            "Попробуй написать текстом."
        )

        print(
            "Ошибка при распознавании голоса:",
            e,
            flush=True
        )

        send_vk_message(
            peer_id,
            reply
        )

        return

    send_vk_message(
        peer_id,
        reply
    )


# =========================================================
# ИЗОБРАЖЕНИЕ
# =========================================================

def handle_image_message(
    peer_id: int,
    from_id: int,
    image_url: str,
    caption: str
):

    try:

        # Фото без вопроса не обрабатываем
        if not should_answer(caption):

            print(
                "Фото без вопроса — игнорируем.",
                flush=True
            )

            return

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
            caption,
            reply
        )

    except Exception as e:

        reply = (
            "Не смог рассмотреть скриншот 😅 "
            "Попробуй ещё раз или опиши словами."
        )

        print(
            "Ошибка при анализе изображения:",
            e,
            flush=True
        )

    send_vk_message(
        peer_id,
        reply
    )


# =========================================================
# ВЫБОР САМОЙ БОЛЬШОЙ ФОТОГРАФИИ VK
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
            "Ошибка получения JSON:",
            e,
            flush=True
        )

        return "bad request", 400

    # -----------------------------------------------------
    # Проверяем secret
    # -----------------------------------------------------

    if (
        VK_GROUP_SECRET
        and data.get("secret") != VK_GROUP_SECRET
    ):

        print(
            "Неверный secret VK",
            flush=True
        )

        return "invalid secret", 403

    event_type = data.get(
        "type"
    )

    # -----------------------------------------------------
    # Подтверждение Callback API
    # -----------------------------------------------------

    if event_type == "confirmation":

        return VK_CONFIRMATION_CODE

    # -----------------------------------------------------
    # Новое сообщение
    # -----------------------------------------------------

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

        # -------------------------------------------------
        # Ищем вложения
        # -------------------------------------------------

        for att in attachments:

            att_type = att.get(
                "type"
            )

            # Голосовое
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

            # Фото
            elif att_type == "photo":

                photo = att.get(
                    "photo",
                    {}
                )

                image_url = get_best_photo_url(
                    photo
                )

        # -------------------------------------------------
        # Голос
        # -------------------------------------------------

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

        # -------------------------------------------------
        # Фото
        # -------------------------------------------------

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

        # -------------------------------------------------
        # Текст
        # -------------------------------------------------

        elif text.strip():

            # Сразу отсеиваем ненужные сообщения,
            # чтобы вообще не запускать отдельный поток
            if should_answer(text):

                threading.Thread(
                    target=handle_message,
                    args=(
                        peer_id,
                        from_id,
                        text
                    ),
                    daemon=True
                ).start()

            else:

                print(
                    "Сообщение без ? — игнорируем:",
                    text,
                    flush=True
                )

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

    app.run(
        host="0.0.0.0",
        port=port
    )
