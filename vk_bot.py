import os
import re
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
# СИСТЕМНЫЙ ПРОМПТ
# =========================================================

SYSTEM_PROMPT = (
    "Ты — ИИ-бот сообщества 'Бонус-коды Tanks blitz' ВКонтакте, посвящённого ИСКЛЮЧИТЕЛЬНО "
    "игре Tanks Blitz PVP битвы (разработчик EAST-GAMES LLC / Lesta Games). Ты — часть "
    "админской команды сообщества, свой парень среди танкистов. Ты не просто справочник, "
    "а участник тусовки: подкалываешь игроков по-дружески, угараешь вместе с ними, "
    "поддерживаешь живой разговор, помнишь, о чём говорили с человеком раньше "
    "(тебе для этого дают историю последних сообщений).\n\n"

    "Если разговор уходит совсем далеко от игры — дерзко и с юмором подкалывай и мягко "
    "возвращай к танкам, но не будь занудой — лёгкий стёб на отвлечённые темы допустим, "
    "если это часть живого общения с человеком. Просто не отвечай по существу на "
    "посторонние вопросы.\n\n"

    "ПАМЯТЬ: используй историю переписки с этим человеком, чтобы вести связный диалог, "
    "шутить над тем, что он говорил раньше, помнить контекст.\n\n"

    "ОБРАЩЕНИЕ ПО ИМЕНИ: тебе передаётся имя пользователя в формате '[Имя: ...]' "
    "в начале сообщения. Обращайся по имени естественно. Саму пометку в ответе не показывай.\n\n"

    "ЗАПРЕТ НА ВЫДУМЫВАНИЕ ТОЧНЫХ ЦИФР: не придумывай точные характеристики техники, "
    "калибры, урон, броню, валюту — ты их не знаешь. За конкретикой отправляй смотреть "
    "гайды на YouTube.\n\n"

    "ФОРМАТ ОТВЕТА: КОРОТКО, максимум 2-3 предложения. "
    "Никаких портянок текста и никаких технических пометок/тегов — только чистый финальный ответ.\n\n"

    "Тон: неформальный, дерзкий, с иронией и подколками, но без грубости и оскорблений "
    "в адрес самого человека. Дерзость — смешная, а не обидная."
)


# =========================================================
# FLASK / GROQ
# =========================================================

app = Flask(__name__)

client = Groq(api_key=GROQ_API_KEY)


# =========================================================
# МОДЕЛИ
# =========================================================

MAIN_MODEL = "openai/gpt-oss-120b"
BACKUP_MODEL = "openai/gpt-oss-20b"

# Модель для анализа изображений
VISION_MODEL = "qwen/qwen3.6-27b"


# Если 120B получил лимит — не пробуем его снова 1 час
MAIN_MODEL_RETRY_TIME = 60 * 60
main_model_blocked_until = 0


# =========================================================
# ПАМЯТЬ ДИАЛОГА
# =========================================================

MAX_HISTORY_MESSAGES = 5

conversation_history = {}
history_lock = threading.Lock()


# =========================================================
# ОЧИСТКА ОТВЕТА
# =========================================================

def clean_response(text: str) -> str:
    """
    Удаляет <think>...</think> и незакрытый <think>
    из ответа модели.
    """

    if not text:
        return ""

    text = str(text).strip()

    # Удаляем полностью закрытые блоки <think>...</think>
    text = re.sub(
        r"<think>.*?</think>",
        "",
        text,
        flags=re.DOTALL | re.IGNORECASE
    )

    # Если модель начала <think>, но не закрыла его,
    # удаляем всё начиная с <think>
    text = re.sub(
        r"<think>.*$",
        "",
        text,
        flags=re.DOTALL | re.IGNORECASE
    )

    # На всякий случай убираем сами теги
    text = re.sub(r"</?think>", "", text, flags=re.IGNORECASE)

    return text.strip()


# =========================================================
# ПРОВЕРКА RATE LIMIT
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
# ПОЛУЧЕНИЕ ИМЕНИ ПОЛЬЗОВАТЕЛЯ
# =========================================================

def get_user_name(user_id: int) -> str:
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

        response.raise_for_status()

        result = response.json()

        if "response" not in result:
            return ""

        if not result["response"]:
            return ""

        first_name = result["response"][0].get("first_name", "")

        return first_name

    except Exception as e:
        print(
            "Не удалось получить имя пользователя:",
            e,
            flush=True
        )

        return ""


# =========================================================
# ИСТОРИЯ
# =========================================================

def get_history(user_id: int):
    with history_lock:
        return list(
            conversation_history.get(user_id, [])
        )


def add_to_history(
    user_id: int,
    user_message: str,
    bot_reply: str
):
    if not user_message or not bot_reply:
        return

    with history_lock:
        history = conversation_history.get(
            user_id,
            []
        )

        history.append({
            "role": "user",
            "content": user_message
        })

        history.append({
            "role": "assistant",
            "content": bot_reply
        })

        max_items = MAX_HISTORY_MESSAGES * 2

        conversation_history[user_id] = history[-max_items:]


# =========================================================
# ЗАПРОС К МОДЕЛИ
# =========================================================

def ask_model(
    model,
    user_message,
    user_name,
    user_id
):

    message_with_name = (
        f"[Имя: {user_name}] {user_message}"
        if user_name
        else user_message
    )

    messages = [
        {
            "role": "system",
            "content": SYSTEM_PROMPT
        }
    ]

    messages.extend(
        get_history(user_id)
    )

    messages.append({
        "role": "user",
        "content": message_with_name
    })

    completion = client.chat.completions.create(
        model=model,
        messages=messages,
        max_tokens=250,
    )

    raw_reply = completion.choices[0].message.content

    reply = clean_response(raw_reply)

    # Если после очистки ничего не осталось
    if not reply:
        raise RuntimeError(
            "Модель вернула пустой ответ"
        )

    add_to_history(
        user_id,
        user_message,
        reply
    )

    return reply


# =========================================================
# GROQ — ОСНОВНАЯ / ЗАПАСНАЯ МОДЕЛЬ
# =========================================================

def ask_groq(
    user_message: str,
    user_name: str,
    user_id: int
) -> str:

    global main_model_blocked_until

    current_time = time.time()

    # -----------------------------------------------------
    # Пробуем 120B
    # -----------------------------------------------------

    if current_time >= main_model_blocked_until:

        try:
            print(
                "Пробуем основную модель:",
                MAIN_MODEL,
                flush=True
            )

            reply = ask_model(
                MAIN_MODEL,
                user_message,
                user_name,
                user_id
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

    # -----------------------------------------------------
    # Запасная модель
    # -----------------------------------------------------

    print(
        "Используем запасную модель:",
        BACKUP_MODEL,
        flush=True
    )

    return ask_model(
        BACKUP_MODEL,
        user_message,
        user_name,
        user_id
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
# АНАЛИЗ ИЗОБРАЖЕНИЯ
# =========================================================

def ask_about_image(
    image_url: str,
    user_name: str,
    user_id: int,
    caption: str = ""
) -> str:

    if caption and caption.strip():
        prompt_text = caption.strip()
    else:
        prompt_text = (
            "Посмотри на этот скриншот из Tanks Blitz "
            "и коротко прокомментируй его в своём стиле."
        )

    message_with_name = (
        f"[Имя: {user_name}] {prompt_text}"
        if user_name
        else prompt_text
    )

    messages = [
        {
            "role": "system",
            "content": SYSTEM_PROMPT
        }
    ]

    # Историю оставляем
    # Но она содержит только обычные текстовые сообщения
    messages.extend(
        get_history(user_id)
    )

    messages.append({
        "role": "user",
        "content": [
            {
                "type": "text",
                "text": message_with_name
            },
            {
                "type": "image_url",
                "image_url": {
                    "url": image_url
                }
            }
        ]
    })

    print(
        "Анализируем изображение через:",
        VISION_MODEL,
        flush=True
    )

    completion = client.chat.completions.create(
        model=VISION_MODEL,
        messages=messages,

        # Короткий ответ
        max_tokens=250,

        # ВАЖНО:
        # полностью отключаем reasoning,
        # чтобы модель не выдавала <think>
        reasoning_effort="none",
    )

    raw_reply = completion.choices[0].message.content

    print(
        "Ответ vision-модели:",
        raw_reply,
        flush=True
    )

    reply = clean_response(raw_reply)

    if not reply:
        raise RuntimeError(
            "Vision-модель вернула пустой ответ"
        )

    add_to_history(
        user_id,
        prompt_text,
        reply
    )

    return reply


# =========================================================
# ОТПРАВКА СООБЩЕНИЯ VK
# =========================================================

def send_vk_message(
    peer_id: int,
    text: str
):

    if not text:
        text = (
            "Что-то я завис 😅 "
            "Попробуй отправить ещё раз."
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

        response.raise_for_status()

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
            "Ошибка отправки сообщения VK:",
            e,
            flush=True
        )

        return None


# =========================================================
# ОБЫЧНОЕ СООБЩЕНИЕ
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
            text,
            user_name,
            from_id
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
# ГОЛОСОВОЕ СООБЩЕНИЕ
# =========================================================

def handle_voice_message(
    peer_id: int,
    from_id: int,
    voice_url: str
):

    try:

        user_name = get_user_name(
            from_id
        )

        text = transcribe_voice(
            voice_url
        )

        print(
            "Распознан голос:",
            text,
            flush=True
        )

        if not text:
            raise RuntimeError(
                "Голосовое сообщение пустое"
            )

        reply = ask_groq(
            text,
            user_name,
            from_id
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

        user_name = get_user_name(
            from_id
        )

        reply = ask_about_image(
            image_url,
            user_name,
            from_id,
            caption
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
# ПОЛУЧЕНИЕ ЛУЧШЕЙ ФОТОГРАФИИ
# =========================================================

def get_best_photo_url(photo):
    """
    VK может отдавать несколько размеров фотографии.
    Берём самый большой доступный.
    """

    if not photo:
        return None

    sizes = photo.get("sizes", [])

    if not sizes:
        return None

    best_size = max(
        sizes,
        key=lambda x: (
            x.get("width", 0)
            * x.get("height", 0)
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

    # -----------------------------------------------------
    # Проверка secret
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

    event_type = data.get("type")

    # -----------------------------------------------------
    # Подтверждение сервера
    # -----------------------------------------------------

    if event_type == "confirmation":

        return VK_CONFIRMATION_CODE

    # -----------------------------------------------------
    # Новое сообщение
    # -----------------------------------------------------

    if event_type == "message_new":

        obj = data.get(
            "object",
            {}
        )

        from_id = obj.get(
            "from_id"
        )

        peer_id = obj.get(
            "peer_id"
        )

        text = (
            obj.get("text")
            or ""
        ).strip()

        if not from_id or not peer_id:

            return "ok"

        # -------------------------------------------------
        # Вложения
        # -------------------------------------------------

        attachments = (
            obj.get("attachments")
            or []
        )

        # -------------------------------------------------
        # Ищем голосовое
        # -------------------------------------------------

        for attachment in attachments:

            if attachment.get("type") == "audio_message":

                audio_message = (
                    attachment.get(
                        "audio_message"
                    )
                    or {}
                )

                voice_url = audio_message.get(
                    "link_ogg"
                )

                if not voice_url:
                    voice_url = audio_message.get(
                        "link_mp3"
                    )

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

                    return "ok"

        # -------------------------------------------------
        # Ищем фотографию
        # -------------------------------------------------

        for attachment in attachments:

            if attachment.get("type") == "photo":

                photo = (
                    attachment.get(
                        "photo"
                    )
                    or {}
                )

                image_url = get_best_photo_url(
                    photo
                )

                if image_url:

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

                    return "ok"

        # -------------------------------------------------
        # Обычный текст
        # -------------------------------------------------

        if text:

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

    # -----------------------------------------------------
    # Все остальные события
    # -----------------------------------------------------

    return "ok"


# =========================================================
# ЗАПУСК
# =========================================================

if __name__ == "__main__":

    port = int(
        os.environ.get(
            "PORT",
            10000
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
