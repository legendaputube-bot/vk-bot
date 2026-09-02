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

# Модель для изображений
VISION_MODEL = "meta-llama/llama-4-scout-17b-16e-instruct"

# Модель для придумывания клички
NICKNAME_MODEL = "openai/gpt-oss-20b"

MAIN_MODEL_RETRY_TIME = 60 * 60

main_model_blocked_until = 0


# =========================================================
# ПАМЯТЬ
# =========================================================

MAX_HISTORY_MESSAGES = 25

# Через сколько секунд пользователь считается уснувшим
SESSION_TIMEOUT = 60

# user_id:
# {
#     "history": [...],
#     "nickname": "...",
#     "active": True/False,
#     "last_activity": timestamp,
#     "lock": threading.Lock()
# }

users_memory = {}

memory_lock = threading.Lock()


# =========================================================
# SYSTEM PROMPT
# =========================================================

SYSTEM_PROMPT = """
Ты — ИИ-бот танкового сообщества ВКонтакте, посвящённого
ИСКЛЮЧИТЕЛЬНО игре Tanks Blitz PVP.

Твоя главная тема — Tanks Blitz, танки, техника, бои,
тактика, игровые механики, обновления и всё, что напрямую
связано с этой игрой.

Ты не универсальный ассистент.
Ты — танковый ИИ сообщества.

=========================================================
ХАРАКТЕР
=========================================================

Ты живой, дерзкий, языкастый и немного наглый танкист.

Используй:
- лёгкую иронию;
- дружеские подколы;
- юмор;
- живую речь.

Но:
- не оскорбляй пользователя;
- не унижай его;
- не переходи на настоящую грубость;
- не используй оскорбительные прозвища.

Ты должен ощущаться как свой человек из танкового чата,
а не как сухой технический бот.

=========================================================
КЛИЧКА ПОЛЬЗОВАТЕЛЯ
=========================================================

В начале новой сессии тебе передаётся:

[Кличка: ...]

Это специально придуманная для пользователя танковая кличка.

ВАЖНО:

Никогда не используй настоящее имя пользователя из VK.

Обращайся только по переданной кличке.

Кличка может быть забавной, танковой или необычной.

Например:
- Стальной Барон
- Гусеничный
- Шальной Командир
- Критовик
- Танковый Псих
- Король рикошетов

Не нужно вставлять кличку абсолютно в каждое предложение.

=========================================================
ПРИВЕТСТВИЕ
=========================================================

Если перед тобой стоит:

[НОВАЯ СЕССИЯ]

это означает, что пользователь снова начал разговор
после периода сна.

В таком случае можешь коротко поздороваться
и обратиться по кличке.

Например:

"О, Стальной Барон снова в ангаре 😎 Что случилось?"

Но если это продолжение уже активного разговора,
не здоровайся снова.

=========================================================
ПАМЯТЬ
=========================================================

Тебе передаётся история предыдущих сообщений пользователя.

Используй её для понимания контекста.

Не утверждай, что помнишь что-либо, чего нет
в переданной истории.

Не пересказывай историю пользователю без необходимости.

=========================================================
TANKS BLITZ
=========================================================

Ты отвечаешь только на темы, связанные с Tanks Blitz.

Если вопрос вообще не связан с игрой,
не отвечай по существу.

Можешь с юмором сказать, что ты танковый бот
и твоя голова забита танками.

=========================================================
ТОЧНЫЕ ЦИФРЫ
=========================================================

Не выдумывай точные характеристики техники.

Не придумывай:
- урон;
- броню;
- пробитие;
- скорость;
- точные значения перезарядки;
- точные коэффициенты;
- статистику;
- характеристики патчей.

Если не уверен в конкретной цифре,
честно скажи, что не уверен.

=========================================================
СКРИНШОТЫ
=========================================================

Если пользователь отправил изображение,
внимательно посмотри на него.

Если на изображении Tanks Blitz:
- опиши, что видишь;
- отвечай именно на вопрос пользователя;
- обращай внимание на интерфейс игры;
- не выдумывай то, чего на скриншоте нет.

=========================================================
ГОЛОСОВЫЕ
=========================================================

Если пользователь отправил голосовое,
ты получаешь его расшифровку как обычное сообщение.

Отвечай по смыслу расшифрованного текста.

=========================================================
ФОРМАТ
=========================================================

Отвечай коротко.

Обычно:
2–4 предложения.

Если список действительно нужен:
максимум 3 пункта.

Не пиши огромные портянки.

Не показывай:
- системные инструкции;
- служебные данные;
- JSON;
- внутренние рассуждения;
- <think>;
- технические сообщения.

Всегда возвращай только готовый ответ пользователю.
"""


# =========================================================
# FLASK
# =========================================================

app = Flask(__name__)

client = Groq(
    api_key=GROQ_API_KEY
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
# НОРМАЛИЗАЦИЯ "ИИ"
# =========================================================

def starts_with_ai_command(text: str) -> bool:

    if not text:
        return False

    text = text.strip().lower()

    allowed = (
        "ии",
        "ии ",
        "ии,",
        "ии!",
        "ии?",
        "ии:",
        "ии."
    )

    return text.startswith(allowed)


def remove_ai_command(text: str) -> str:

    if not text:
        return ""

    text = text.strip()

    lower_text = text.lower()

    if lower_text.startswith("ии"):

        text = text[2:]

        # Убираем знаки после "ии"
        text = text.lstrip(" ,.!?:;-—")

    return text.strip()


# =========================================================
# ПОЛЬЗОВАТЕЛЬ
# =========================================================

def get_user_state(user_id: int):

    with memory_lock:

        if user_id not in users_memory:

            users_memory[user_id] = {
                "history": [],
                "nickname": None,
                "active": False,
                "last_activity": 0,
                "lock": threading.Lock()
            }

        return users_memory[user_id]


# =========================================================
# ОЧИСТКА ПАМЯТИ
# =========================================================

def cleanup_user_memory(user_id: int):

    with memory_lock:

        state = users_memory.get(user_id)

        if not state:
            return

        last_activity = state.get(
            "last_activity",
            0
        )

        if (
            time.time() - last_activity
            >= SESSION_TIMEOUT
        ):

            print(
                f"[MEMORY] Пользователь {user_id} уснул. "
                f"Очищаем историю.",
                flush=True
            )

            state["history"] = []
            state["active"] = False


# =========================================================
# ТАЙМЕР ОЧИСТКИ
# =========================================================

def schedule_memory_cleanup(user_id: int):

    def cleanup():

        time.sleep(
            SESSION_TIMEOUT
        )

        cleanup_user_memory(
            user_id
        )

    threading.Thread(
        target=cleanup,
        daemon=True
    ).start()


# =========================================================
# VK ИМЯ
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

        result = response.json()

        first_name = (
            result["response"][0]["first_name"]
        )

        return first_name

    except Exception as e:

        print(
            "Не удалось получить имя пользователя:",
            e,
            flush=True
        )

        return ""


# =========================================================
# СОЗДАНИЕ КЛИЧКИ
# =========================================================

def generate_nickname(user_id: int) -> str:

    try:

        prompt = """
Придумай короткую танковую кличку для участника
сообщества Tanks Blitz.

Кличка должна быть:
- дружеской;
- запоминающейся;
- немного смешной;
- связанной с танками или боями.

Не используй имя человека.
Не используй оскорбления.
Не используй мат.
Не делай кличку слишком длинной.

Верни ТОЛЬКО кличку.
Максимум 3 слова.
"""

        completion = client.chat.completions.create(
            model=NICKNAME_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": prompt
                }
            ],
            max_tokens=30,
        )

        nickname = (
            completion
            .choices[0]
            .message
            .content
        )

        if not nickname:
            raise RuntimeError(
                "Модель не вернула кличку"
            )

        nickname = nickname.strip()

        # Убираем возможные кавычки
        nickname = nickname.strip(
            "\"'«»"
        )

        if len(nickname) > 40:
            nickname = nickname[:40].strip()

        print(
            f"[NICKNAME] {user_id} -> {nickname}",
            flush=True
        )

        return nickname

    except Exception as e:

        print(
            "Ошибка создания клички:",
            e,
            flush=True
        )

        return "Танкист"


# =========================================================
# ПОЛУЧЕНИЕ / СОЗДАНИЕ КЛИЧКИ
# =========================================================

def get_user_nickname(user_id: int) -> str:

    state = get_user_state(
        user_id
    )

    if state["nickname"]:
        return state["nickname"]

    nickname = generate_nickname(
        user_id
    )

    state["nickname"] = nickname

    return nickname


# =========================================================
# ДОБАВЛЕНИЕ В ПАМЯТЬ
# =========================================================

def add_to_history(
    user_id: int,
    role: str,
    content: str
):

    state = get_user_state(
        user_id
    )

    state["history"].append(
        {
            "role": role,
            "content": content
        }
    )

    # Максимум 25 сообщений
    if len(state["history"]) > MAX_HISTORY_MESSAGES:

        state["history"] = (
            state["history"][
                -MAX_HISTORY_MESSAGES:
            ]
        )


# =========================================================
# ПОЛУЧЕНИЕ ИСТОРИИ
# =========================================================

def get_history(user_id: int):

    state = get_user_state(
        user_id
    )

    return list(
        state["history"]
    )


# =========================================================
# ОБЫЧНАЯ МОДЕЛЬ
# =========================================================

def ask_model(
    model,
    user_message,
    user_id,
    is_new_session=False
):

    nickname = get_user_nickname(
        user_id
    )

    history = get_history(
        user_id
    )

    messages = [
        {
            "role": "system",
            "content": SYSTEM_PROMPT
        }
    ]

    # Передаём кличку
    messages.append(
        {
            "role": "system",
            "content": (
                f"[Кличка: {nickname}]\n"
                + (
                    "[НОВАЯ СЕССИЯ]"
                    if is_new_session
                    else ""
                )
            )
        }
    )

    # Старый контекст
    messages.extend(
        history
    )

    # Новый вопрос
    messages.append(
        {
            "role": "user",
            "content": user_message
        }
    )

    completion = client.chat.completions.create(
        model=model,
        messages=messages,
        max_tokens=300,
    )

    reply = (
        completion
        .choices[0]
        .message
        .content
    )

    if not reply:
        raise RuntimeError(
            "Модель вернула пустой ответ"
        )

    return reply.strip()


# =========================================================
# GROQ — ОСНОВНАЯ / ЗАПАСНАЯ
# =========================================================

def ask_groq(
    user_message: str,
    user_id: int,
    is_new_session=False
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
                user_message,
                user_id,
                is_new_session
            )

            main_model_blocked_until = 0

            print(
                "120B работает.",
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
        "Используем:",
        BACKUP_MODEL,
        flush=True
    )

    return ask_model(
        BACKUP_MODEL,
        user_message,
        user_id,
        is_new_session
    )


# =========================================================
# ГОЛОСОВОЕ
# =========================================================

def transcribe_voice(
    audio_url: str
) -> str:

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
# СКАЧИВАНИЕ ИЗОБРАЖЕНИЯ
# =========================================================

def download_image_as_base64(
    image_url: str
):

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

    if not content_type.startswith(
        "image/"
    ):

        content_type = "image/jpeg"

    encoded_image = (
        base64
        .b64encode(image_data)
        .decode("utf-8")
    )

    data_url = (
        f"data:{content_type};base64,{encoded_image}"
    )

    print(
        "Изображение успешно загружено:",
        round(
            len(image_data) / 1024,
            1
        ),
        "KB",
        flush=True
    )

    return data_url


# =========================================================
# АНАЛИЗ ИЗОБРАЖЕНИЯ
# =========================================================

def ask_about_image(
    image_url: str,
    user_id: int,
    caption: str = "",
    is_new_session=False
) -> str:

    nickname = get_user_nickname(
        user_id
    )

    history = get_history(
        user_id
    )

    if caption and caption.strip():

        prompt_text = caption.strip()

    else:

        prompt_text = (
            "Посмотри на этот скриншот из "
            "Tanks Blitz. Коротко скажи, "
            "что на нём происходит."
        )

    image_data_url = (
        download_image_as_base64(
            image_url
        )
    )

    print(
        "Отправляем изображение в:",
        VISION_MODEL,
        flush=True
    )

    messages = [
        {
            "role": "system",
            "content": SYSTEM_PROMPT
        },
        {
            "role": "system",
            "content": (
                f"[Кличка: {nickname}]\n"
                + (
                    "[НОВАЯ СЕССИЯ]"
                    if is_new_session
                    else ""
                )
            )
        }
    ]

    messages.extend(
        history
    )

    messages.append(
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": prompt_text
                },
                {
                    "type": "image_url",
                    "image_url": {
                        "url": image_data_url
                    }
                }
            ]
        }
    )

    completion = client.chat.completions.create(
        model=VISION_MODEL,
        messages=messages,
        max_tokens=300,
    )

    reply = (
        completion
        .choices[0]
        .message
        .content
    )

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
# АКТИВАЦИЯ СЕССИИ
# =========================================================

def activate_user(
    user_id: int
):

    state = get_user_state(
        user_id
    )

    was_active = state["active"]

    state["active"] = True
    state["last_activity"] = time.time()

    return not was_active


# =========================================================
# ТЕКСТОВОЕ СООБЩЕНИЕ
# =========================================================

def handle_message(
    peer_id: int,
    from_id: int,
    text: str
):

    state = get_user_state(
        from_id
    )

    # Защита от параллельных сообщений
    with state["lock"]:

        try:

            # Если человек уснул
            if (
                time.time()
                - state["last_activity"]
                >= SESSION_TIMEOUT
            ):

                state["history"] = []
                state["active"] = False

            # -------------------------------------------------
            # Проверяем "ии"
            # -------------------------------------------------

            if not starts_with_ai_command(
                text
            ):

                print(
                    f"[IGNORE] {from_id}: "
                    f"нет обращения 'ии'",
                    flush=True
                )

                return

            # -------------------------------------------------
            # Убираем "ии"
            # -------------------------------------------------

            user_text = remove_ai_command(
                text
            )

            if not user_text:

                print(
                    f"[IGNORE] {from_id}: "
                    f"после 'ии' нет вопроса",
                    flush=True
                )

                return

            # -------------------------------------------------
            # Активируем сессию
            # -------------------------------------------------

            is_new_session = activate_user(
                from_id
            )

            # -------------------------------------------------
            # Добавляем вопрос
            # -------------------------------------------------

            add_to_history(
                from_id,
                "user",
                user_text
            )

            # -------------------------------------------------
            # Получаем ответ
            # -------------------------------------------------

            reply = ask_groq(
                user_text,
                from_id,
                is_new_session
            )

            # -------------------------------------------------
            # Сохраняем ответ
            # -------------------------------------------------

            add_to_history(
                from_id,
                "assistant",
                reply
            )

            state["last_activity"] = time.time()

            # -------------------------------------------------
            # Отправляем
            # -------------------------------------------------

            send_vk_message(
                peer_id,
                reply
            )

            schedule_memory_cleanup(
                from_id
            )

        except Exception as e:

            print(
                "Ошибка при обработке сообщения:",
                e,
                flush=True
            )

            send_vk_message(
                peer_id,
                "Что-то я сейчас подвис 😅 "
                "Попробуй ещё раз."
            )


# =========================================================
# ГОЛОС
# =========================================================

def handle_voice_message(
    peer_id: int,
    from_id: int,
    voice_url: str
):

    state = get_user_state(
        from_id
    )

    with state["lock"]:

        try:

            # -------------------------------------------------
            # Расшифровываем
            # -------------------------------------------------

            text = transcribe_voice(
                voice_url
            )

            print(
                f"[VOICE] {from_id}: {text}",
                flush=True
            )

            if not text:
                return

            # -------------------------------------------------
            # Голосовое тоже должно начинаться с ИИ
            # -------------------------------------------------

            if not starts_with_ai_command(
                text
            ):

                print(
                    f"[IGNORE VOICE] {from_id}: "
                    f"нет 'ии'",
                    flush=True
                )

                return

            user_text = remove_ai_command(
                text
            )

            if not user_text:
                return

            # -------------------------------------------------
            # Проверяем сон
            # -------------------------------------------------

            if (
                time.time()
                - state["last_activity"]
                >= SESSION_TIMEOUT
            ):

                state["history"] = []
                state["active"] = False

            is_new_session = activate_user(
                from_id
            )

            add_to_history(
                from_id,
                "user",
                user_text
            )

            reply = ask_groq(
                user_text,
                from_id,
                is_new_session
            )

            add_to_history(
                from_id,
                "assistant",
                reply
            )

            state["last_activity"] = time.time()

            send_vk_message(
                peer_id,
                reply
            )

            schedule_memory_cleanup(
                from_id
            )

        except Exception as e:

            print(
                "Ошибка голосового:",
                e,
                flush=True
            )

            send_vk_message(
                peer_id,
                "Не смог разобрать голосовое 😅 "
                "Попробуй ещё раз."
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

    state = get_user_state(
        from_id
    )

    with state["lock"]:

        try:

            # -------------------------------------------------
            # Фото без текста "ии" игнорируем
            # -------------------------------------------------

            if not starts_with_ai_command(
                caption
            ):

                print(
                    f"[IGNORE PHOTO] {from_id}: "
                    f"нет 'ии'",
                    flush=True
                )

                return

            user_text = remove_ai_command(
                caption
            )

            # -------------------------------------------------
            # Если после ИИ ничего нет
            # -------------------------------------------------

            if not user_text:

                user_text = (
                    "Посмотри на этот скриншот "
                    "из Tanks Blitz и коротко "
                    "скажи, что на нём происходит."
                )

            # -------------------------------------------------
            # Проверяем сон
            # -------------------------------------------------

            if (
                time.time()
                - state["last_activity"]
                >= SESSION_TIMEOUT
            ):

                state["history"] = []
                state["active"] = False

            is_new_session = activate_user(
                from_id
            )

            # -------------------------------------------------
            # Сохраняем вопрос
            # -------------------------------------------------

            add_to_history(
                from_id,
                "user",
                user_text
            )

            # -------------------------------------------------
            # Vision
            # -------------------------------------------------

            reply = ask_about_image(
                image_url,
                from_id,
                user_text,
                is_new_session
            )

            # -------------------------------------------------
            # Сохраняем ответ
            # -------------------------------------------------

            add_to_history(
                from_id,
                "assistant",
                reply
            )

            state["last_activity"] = time.time()

            send_vk_message(
                peer_id,
                reply
            )

            schedule_memory_cleanup(
                from_id
            )

        except Exception as e:

            print(
                "Ошибка изображения:",
                e,
                flush=True
            )

            send_vk_message(
                peer_id,
                "Не смог рассмотреть скриншот 😅 "
                "Попробуй ещё раз."
            )


# =========================================================
# ЛУЧШЕЕ ФОТО VK
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

    return best_size.get(
        "url"
    )


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

    # =====================================================
    # SECRET
    # =====================================================

    if (
        VK_GROUP_SECRET
        and data.get("secret")
        != VK_GROUP_SECRET
    ):

        print(
            "Неверный secret VK",
            flush=True
        )

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
    # MESSAGE NEW
    # =====================================================

    if event_type == "message_new":

        message = (
            data
            .get("object", {})
            .get("message", {})
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

        # -------------------------------------------------
        # Бот не отвечает сам себе
        # -------------------------------------------------

        if from_id < 0:

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
            # Голосовое
            # ---------------------------------------------

            if att_type == "audio_message":

                audio_message = att.get(
                    "audio_message",
                    {}
                )

                voice_url = (
                    audio_message.get(
                        "link_ogg"
                    )
                    or
                    audio_message.get(
                        "link_mp3"
                    )
                )

            # ---------------------------------------------
            # Фото
            # ---------------------------------------------

            elif att_type == "photo":

                photo = att.get(
                    "photo",
                    {}
                )

                image_url = (
                    get_best_photo_url(
                        photo
                    )
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
        # ТЕКСТ
        # =================================================

        elif text.strip():

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
        "========================================",
        flush=True
    )

    print(
        "VK AI БОТ ЗАПУСКАЕТСЯ",
        flush=True
    )

    print(
        f"Основная модель: {MAIN_MODEL}",
        flush=True
    )

    print(
        f"Запасная модель: {BACKUP_MODEL}",
        flush=True
    )

    print(
        f"Vision: {VISION_MODEL}",
        flush=True
    )

    print(
        f"Память: {MAX_HISTORY_MESSAGES} сообщений",
        flush=True
    )

    print(
        f"Сон: {SESSION_TIMEOUT} секунд",
        flush=True
    )

    print(
        "Команда обращения: ИИ",
        flush=True
    )

    print(
        "========================================",
        flush=True
    )

    app.run(
        host="0.0.0.0",
        port=port
    )
