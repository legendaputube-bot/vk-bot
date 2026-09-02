import os
import base64
import requests
import time
import threading
import json
from datetime import datetime
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
    "'[Имя: ...]'. Обращайся к человеку по этому имени, естественно вписывая его в дерзкий стиль. "
    "Саму пометку '[Имя: ...]' в ответе не показывай.\n\n"

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


# =========================================================
# НАСТРОЙКИ
# =========================================================

app = Flask(__name__)
client = Groq(api_key=GROQ_API_KEY)

VK_API_URL = "https://api.vk.com/method/messages.send"
VK_USERS_GET_URL = "https://api.vk.com/method/users.get"
VK_API_VERSION = "5.199"

MAIN_MODEL = "openai/gpt-oss-120b"
BACKUP_MODEL = "openai/gpt-oss-20b"

VISION_MODEL = "meta-llama/llama-4-scout-17b-16e-instruct"

MAIN_MODEL_RETRY_TIME = 60 * 60
main_model_blocked_until = 0

# ID главного администратора
ADMIN_ID = 948950706

# Файл памяти модерации
MODERATION_MEMORY_FILE = "moderation_memory.json"


# =========================================================
# ПРАВИЛА ЧАТА
# =========================================================

MODERATION_RULES = """
ПРАВИЛА МОДЕРАЦИИ ЧАТА:

3.3. РЕКЛАМА
Запрещена любая реклама, а также приглашения в сторонние чаты,
каналы и сообщества без согласования с администрацией.

Одобренные каналы:
- Бонус-коды Tanks Blitz
- Tanks Blitz

4.1. ИГРОВЫЕ АККАУНТЫ
Запрещены покупка, продажа, обмен, передача или дарение игровых аккаунтов.
Обсуждение подобных действий в общем чате запрещено.

4.2. РОЗЫГРЫШИ И КОНКУРСЫ
Любые розыгрыши, акции и конкурсы должны быть заранее согласованы
с главным администратором.

4.3. ОБСУЖДЕНИЕ РОЗЫГРЫШЕЙ
Вопросы по проведению розыгрышей обсуждаются только с администрацией
в личных сообщениях.

5.1. ВЫДАЧА СЕБЯ ЗА АДМИНИСТРАЦИЮ
Запрещено выдавать себя за администратора, модератора
или представителя сообщества.

5.2. ЛИЧНЫЕ ДАННЫЕ
Запрещено публиковать чужие личные данные, переписки
или другую личную информацию без согласия участников.

7.3. ПУБЛИЧНЫЕ СПОРЫ
Запрещены публичные споры с администрацией и выяснение отношений
в общем чате.
"""


# =========================================================
# ПАМЯТЬ НАРУШЕНИЙ
# =========================================================

def load_moderation_memory():
    try:
        if not os.path.exists(MODERATION_MEMORY_FILE):
            return {}

        with open(
            MODERATION_MEMORY_FILE,
            "r",
            encoding="utf-8"
        ) as file:
            data = json.load(file)

            if isinstance(data, dict):
                return data

    except Exception as e:
        print(
            "Ошибка загрузки памяти модерации:",
            e,
            flush=True
        )

    return {}


moderation_memory = load_moderation_memory()

memory_lock = threading.Lock()


def save_moderation_memory():

    try:

        with memory_lock:

            with open(
                MODERATION_MEMORY_FILE,
                "w",
                encoding="utf-8"
            ) as file:

                json.dump(
                    moderation_memory,
                    file,
                    ensure_ascii=False,
                    indent=2
                )

    except Exception as e:

        print(
            "Ошибка сохранения памяти:",
            e,
            flush=True
        )


def get_user_moderation(user_id):

    user_id = str(user_id)

    if user_id not in moderation_memory:

        moderation_memory[user_id] = {
            "warnings": 0,
            "violations": []
        }

    return moderation_memory[user_id]


def add_violation(
    user_id,
    reason,
    message
):

    user_id = str(user_id)

    with memory_lock:

        user_data = get_user_moderation(user_id)

        user_data["warnings"] += 1

        violation = {
            "time": datetime.now().strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
            "reason": reason,
            "message": message[:1000]
        }

        user_data["violations"].append(
            violation
        )

        # Храним последние 4 нарушения
        user_data["violations"] = (
            user_data["violations"][-4:]
        )

        warnings = user_data["warnings"]

    save_moderation_memory()

    return warnings


# =========================================================
# ПРОВЕРКА НАРУШЕНИЯ
# =========================================================

def moderate_message(text):

    moderation_prompt = f"""
Ты — система модерации чата сообщества Tanks Blitz.

Твоя задача — определить, нарушает ли сообщение пользователя
правила ниже.

ВАЖНО:
- Анализируй смысл и контекст сообщения.
- Не считай нарушением простое упоминание запрещённой темы,
  если человек обсуждает правила, задаёт вопрос или говорит
  о проблеме без намерения нарушить правило.
- Не придумывай нарушение.
- Если нарушение не очевидно — ставь violation=false.
- Обычный мат сам по себе не является отдельным правилом,
  если из правил выше он напрямую не следует.
- Шутку не считай нарушением без явной причины.
- Если человек реально рекламирует сторонний ресурс,
  приглашает в сторонний чат/канал или размещает рекламу —
  это нарушение 3.3.
- Продажу/покупку/обмен/передачу аккаунтов считай нарушением 4.1.
- Несогласованный конкурс или розыгрыш считай нарушением 4.2.
- Публичное обсуждение проведения розыгрыша считай 4.3,
  если это именно организация/проведение, а не обычный разговор.
- Выдачу себя за администрацию считай нарушением 5.1.
- Чужие личные данные/переписки без согласия — 5.2.
- Публичный конфликт с администрацией — 7.3.

{MODERATION_RULES}

СООБЩЕНИЕ ПОЛЬЗОВАТЕЛЯ:
{text}

Верни ТОЛЬКО JSON без дополнительного текста:

{{
  "violation": true или false,
  "rule": "номер правила или null",
  "reason": "краткая причина или null"
}}
"""

    try:

        completion = client.chat.completions.create(
            model=BACKUP_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": moderation_prompt
                },
                {
                    "role": "user",
                    "content": text
                }
            ],
            max_tokens=150,
            temperature=0
        )

        result = (
            completion.choices[0]
            .message.content
            .strip()
        )

        # Убираем markdown JSON, если модель его всё-таки добавила
        result = result.replace(
            "```json",
            ""
        ).replace(
            "```",
            ""
        ).strip()

        data = json.loads(result)

        return {
            "violation": bool(
                data.get("violation", False)
            ),
            "rule": data.get("rule"),
            "reason": data.get("reason")
        }

    except Exception as e:

        print(
            "Ошибка модерации:",
            e,
            flush=True
        )

        # При ошибке НЕ выдаём ложное предупреждение
        return {
            "violation": False,
            "rule": None,
            "reason": None
        }


# =========================================================
# УПОМИНАНИЕ VK
# =========================================================

def make_mention(
    user_id,
    user_name=""
):

    if user_name:
        return f"[id{user_id}|{user_name}]"

    return f"[id{user_id}|пользователь]"


# =========================================================
# СООБЩЕНИЕ О НАРУШЕНИИ
# =========================================================

def send_warning(
    peer_id,
    from_id,
    user_name,
    rule,
    reason,
    warning_number
):

    mention = make_mention(
        from_id,
        user_name
    )

    if warning_number == 1:

        text = (
            f"{mention}, братан 😅 Полегче. "
            f"Это нарушение правил чата ({rule}).\n"
            f"Причина: {reason}\n\n"
            f"⚠️ Предупреждение: 1/3"
        )

    elif warning_number == 2:

        text = (
            f"{mention}, я тебя уже предупреждал 😐\n"
            f"Нарушение: {rule} — {reason}\n\n"
            f"⚠️ Предупреждение: 2/3\n"
            f"Ещё одно нарушение — вызываю администрацию."
        )

    elif warning_number == 3:

        text = (
            f"{mention}, всё, братан 😐 "
            f"Три предупреждения.\n"
            f"Нарушение: {rule} — {reason}\n\n"
            f"🚨 Предупреждение: 3/3\n"
            f"Вызываю администрацию."
        )

    else:

        text = (
            f"{mention}, нарушение правил: {rule}.\n"
            f"Причина: {reason}\n\n"
            f"🚨 У тебя уже 3 предупреждения. "
            f"Передаю информацию администрации."
        )

    send_vk_message(
        peer_id,
        text
    )


# =========================================================
# УВЕДОМЛЕНИЕ АДМИНИСТРАЦИИ
# =========================================================

def notify_admin(
    peer_id,
    from_id,
    user_name,
    reason,
    rule,
    warning_number,
    message
):

    admin_mention = make_mention(
        ADMIN_ID,
        "Blitz"
    )

    user_mention = make_mention(
        from_id,
        user_name
    )

    text = (
        f"🚨 {admin_mention}\n"
        f"Требуется внимание администрации.\n\n"
        f"👤 Пользователь: {user_mention}\n"
        f"⚠️ Предупреждений: {warning_number}\n"
        f"📋 Правило: {rule}\n"
        f"📝 Причина: {reason}\n\n"
        f"💬 Сообщение:\n"
        f"{message[:1000]}"
    )

    send_vk_message(
        peer_id,
        text
    )


# =========================================================
# КОМАНДА ПРОСМОТРА ПРЕДУПРЕЖДЕНИЙ
# =========================================================

def handle_admin_command(
    peer_id,
    from_id,
    text
):

    if from_id != ADMIN_ID:
        return False

    parts = text.strip().split()

    if not parts:
        return False

    command = parts[0].lower()

    # -----------------------------------------------------
    # /warns ID
    # -----------------------------------------------------

    if command == "/warns":

        if len(parts) < 2:

            send_vk_message(
                peer_id,
                "Использование: /warns ID пользователя"
            )

            return True

        target_id = parts[1]

        data = moderation_memory.get(
            target_id
        )

        if not data:

            send_vk_message(
                peer_id,
                f"📋 У пользователя {target_id} нарушений не найдено."
            )

            return True

        warnings = data.get(
            "warnings",
            0
        )

        violations = data.get(
            "violations",
            []
        )

        lines = [
            f"📋 История нарушений пользователя "
            f"[id{target_id}|ID {target_id}]",
            "",
            f"⚠️ Всего предупреждений: {warnings}",
            ""
        ]

        if not violations:

            lines.append(
                "Нарушений в памяти нет."
            )

        else:

            for index, violation in enumerate(
                violations,
                1
            ):

                lines.append(
                    f"{index}. "
                    f"{violation.get('time', '')}"
                )

                lines.append(
                    f"Правило: "
                    f"{violation.get('reason', 'не указано')}"
                )

                lines.append(
                    f"Сообщение: "
                    f"{violation.get('message', '')}"
                )

                lines.append("")

        send_vk_message(
            peer_id,
            "\n".join(lines)
        )

        return True

    # -----------------------------------------------------
    # /clearwarns ID
    # -----------------------------------------------------

    if command == "/clearwarns":

        if len(parts) < 2:

            send_vk_message(
                peer_id,
                "Использование: /clearwarns ID пользователя"
            )

            return True

        target_id = parts[1]

        with memory_lock:

            moderation_memory.pop(
                target_id,
                None
            )

        save_moderation_memory()

        send_vk_message(
            peer_id,
            f"✅ Предупреждения пользователя {target_id} очищены."
        )

        return True

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
    )


# =========================================================
# ИМЯ ПОЛЬЗОВАТЕЛЯ
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
# ОБЫЧНАЯ МОДЕЛЬ
# =========================================================

def ask_model(
    model,
    user_message,
    user_name
):

    message_with_name = (
        f"[Имя: {user_name}] {user_message}"
        if user_name
        else user_message
    )

    completion = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "system",
                "content": SYSTEM_PROMPT
            },
            {
                "role": "user",
                "content": message_with_name
            },
        ],
        max_tokens=300,
    )

    reply = (
        completion.choices[0]
        .message.content
    )

    if not reply:

        raise RuntimeError(
            "Модель вернула пустой ответ"
        )

    return reply.strip()


# =========================================================
# GROQ — ОСНОВНАЯ / ЗАПАСНАЯ МОДЕЛЬ
# =========================================================

def ask_groq(
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
        user_message,
        user_name
    )


# =========================================================
# ГОЛОСОВЫЕ
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
# СКАЧИВАНИЕ ИЗОБРАЖЕНИЯ VK
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

    reply = (
        completion.choices[0]
        .message.content
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
# ТЕКСТ
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

        # -------------------------------------------------
        # Команды администрации
        # -------------------------------------------------

        if handle_admin_command(
            peer_id,
            from_id,
            text
        ):

            return

        # -------------------------------------------------
        # ЕСЛИ ЭТО ЧАТ — ПРОВЕРЯЕМ ПРАВИЛА
        # -------------------------------------------------

        is_chat = (
            peer_id >= 2000000000
        )

        if (
            is_chat
            and from_id != ADMIN_ID
            and text.strip()
        ):

            moderation = moderate_message(
                text
            )

            if moderation["violation"]:

                warning_number = add_violation(
                    from_id,
                    moderation["reason"]
                    or "Нарушение правил",
                    text
                )

                send_warning(
                    peer_id,
                    from_id,
                    user_name,
                    moderation["rule"]
                    or "правила чата",
                    moderation["reason"]
                    or "нарушение правил",
                    warning_number
                )

                # После 3-го предупреждения
                if warning_number >= 3:

                    notify_admin(
                        peer_id,
                        from_id,
                        user_name,
                        moderation["reason"]
                        or "нарушение правил",
                        moderation["rule"]
                        or "правила чата",
                        warning_number,
                        text
                    )

                # После нарушения бот НЕ отвечает
                # обычным AI-ответом на это сообщение.
                return

        # -------------------------------------------------
        # Обычный ответ бота
        # -------------------------------------------------

        reply = ask_groq(
            text,
            user_name
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
                "Не удалось распознать голос"
            )

        # Проверяем голосовое в чате
        is_chat = (
            peer_id >= 2000000000
        )

        if (
            is_chat
            and from_id != ADMIN_ID
        ):

            moderation = moderate_message(
                text
            )

            if moderation["violation"]:

                warning_number = add_violation(
                    from_id,
                    moderation["reason"]
                    or "Нарушение правил",
                    text
                )

                send_warning(
                    peer_id,
                    from_id,
                    user_name,
                    moderation["rule"]
                    or "правила чата",
                    moderation["reason"]
                    or "нарушение правил",
                    warning_number
                )

                if warning_number >= 3:

                    notify_admin(
                        peer_id,
                        from_id,
                        user_name,
                        moderation["reason"]
                        or "нарушение правил",
                        moderation["rule"]
                        or "правила чата",
                        warning_number,
                        text
                    )

                return

        reply = ask_groq(
            text,
            user_name
        )

    except Exception as e:

        reply = (
            "Не смог разобрать голосовое 😅 "
            "Попробуй написать текстом."
        )

        print(
            "Ошибка при распознавании голосового:",
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

        # Если к фотографии есть подпись,
        # проверяем её на нарушения.
        is_chat = (
            peer_id >= 2000000000
        )

        if (
            is_chat
            and from_id != ADMIN_ID
            and caption.strip()
        ):

            moderation = moderate_message(
                caption
            )

            if moderation["violation"]:

                warning_number = add_violation(
                    from_id,
                    moderation["reason"]
                    or "Нарушение правил",
                    caption
                )

                send_warning(
                    peer_id,
                    from_id,
                    user_name,
                    moderation["rule"]
                    or "правила чата",
                    moderation["reason"]
                    or "нарушение правил",
                    warning_number
                )

                if warning_number >= 3:

                    notify_admin(
                        peer_id,
                        from_id,
                        user_name,
                        moderation["reason"]
                        or "Нарушение правил",
                        moderation["rule"]
                        or "правила чата",
                        warning_number,
                        caption
                    )

                return

        reply = ask_about_image(
            image_url,
            user_name,
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
        "Система модерации активна.",
        flush=True
    )

    print(
        "Администратор:",
        ADMIN_ID,
        flush=True
    )

    app.run(
        host="0.0.0.0",
        port=port
    )
