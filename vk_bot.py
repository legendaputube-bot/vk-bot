import os
import base64
import requests
import time
import threading
import json
from datetime import datetime
from flask import Flask, request
from groq import Groq


# =========================================================
# ENV
# =========================================================

VK_TOKEN = os.environ.get("VK_TOKEN", "")
VK_CONFIRMATION_CODE = os.environ.get("VK_CONFIRMATION_CODE", "")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
VK_GROUP_SECRET = os.environ.get("VK_GROUP_SECRET", "")


# =========================================================
# ОСНОВНОЙ SYSTEM PROMPT
# ИСПОЛЬЗУЕТСЯ ТОЛЬКО В ЛС
# =========================================================

SYSTEM_PROMPT = (
    "Ты — дерзкий, языкастый бот сообщества ВКонтакте, посвящённого "
    "ИСКЛЮЧИТЕЛЬНО игре Tanks Blitz PVP битвы (разработчик EAST-GAMES LLC / Lesta Games) — "
    "мобильному танковому PVP-шутеру. Это твоё единственное разрешённое направление разговора. "
    "Если вопрос не связан с этой игрой — дерзко и с юмором отказывайся отвечать по существу, "
    "напоминай, что тут говорят только про танки.\n\n"

    "ОБРАЩЕНИЕ ПО ИМЕНИ: тебе в начале сообщения передаётся имя пользователя в формате "
    "'[Имя: ...]'. Обращайся к человеку по этому имени естественно. "
    "Саму пометку '[Имя: ...]' в ответе не показывай.\n\n"

    "ЗАПРЕТ НА ВЫДУМЫВАНИЕ ТОЧНЫХ ЦИФР: не придумывай точные характеристики техники, "
    "калибры, урон, броню, названия валюты и другие конкретные цифры. "
    "Если спрашивают про конкретные характеристики техники или что качать — отвечай в общих "
    "чертах и советуй посмотреть актуальные гайды и обзоры техники на YouTube.\n\n"

    "ФОРМАТ ОТВЕТА: отвечай КОРОТКО, максимум 2-3 предложения "
    "или максимум 3 пункта списком. Никаких длинных портянок текста.\n\n"

    "Используй неформальный тон, лёгкую иронию и подколки, но без грубости и оскорблений. "
    "Не хами по-настоящему и не переходи на личности."
)


# =========================================================
# НАСТРОЙКИ
# =========================================================

app = Flask(__name__)
client = Groq(api_key=GROQ_API_KEY)

VK_API_URL = "https://api.vk.com/method/messages.send"
VK_USERS_GET_URL = "https://api.vk.com/method/users.get"
VK_API_VERSION = "5.199"

# Обычный ИИ
MAIN_MODEL = "openai/gpt-oss-120b"
BACKUP_MODEL = "openai/gpt-oss-20b"

# Модерация
MODERATION_MODEL = "openai/gpt-oss-20b"

# Vision
VISION_MODEL = "qwen/qwen3.6-27b"

MAIN_MODEL_RETRY_TIME = 60 * 60
main_model_blocked_until = 0

ADMIN_ID = 948950706

MODERATION_MEMORY_FILE = "moderation_memory.json"


# =========================================================
# ЛИМИТЫ ТОКЕНОВ
# =========================================================

TEXT_MAX_TOKENS = 200
VOICE_MAX_TOKENS = 150
PHOTO_MAX_TOKENS = 130
MODERATION_MAX_TOKENS = 300


# =========================================================
# ПРАВИЛА ЧАТА
# =========================================================

MODERATION_RULES = """
ПРАВИЛА ЧАТА:

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

6.1. РАБОТА АДМИНИСТРАЦИИ
Администрация поддерживает порядок и действует
в интересах сообщества.

6.2. ПОЛНОМОЧИЯ АДМИНИСТРАЦИИ
Администрация имеет право удалять сообщения,
выдавать предупреждения, муты и блокировки.

6.3. ИЗМЕНЕНИЕ ПРАВИЛ
Правила могут быть изменены или дополнены администрацией.

6.4. РЕШЕНИЕ ГЛАВНОГО АДМИНИСТРАТОРА
Решение главного администратора является окончательным.

7.1. ЖАЛОБЫ
По вопросам и жалобам обращаться к администрации.

7.2. ОБЖАЛОВАНИЕ НАКАЗАНИЯ
Обжалование предупреждений и других наказаний —
в личных сообщениях главному администратору.

7.3. ПУБЛИЧНЫЕ СПОРЫ
Запрещены публичные споры с администрацией
и выяснение отношений в общем чате.
"""


RULES_TEXT = (
    "📋 ПРАВИЛА ЧАТА\n\n"

    "3.3. Реклама — запрещена реклама и приглашения "
    "в сторонние чаты, каналы и сообщества без согласования.\n\n"

    "4.1. Игровые аккаунты — запрещены покупка, продажа, "
    "обмен, передача и дарение аккаунтов, а также обсуждение "
    "таких действий в общем чате.\n\n"

    "4.2. Розыгрыши и конкурсы — только с предварительным "
    "согласованием главного администратора.\n\n"

    "4.3. Обсуждение розыгрышей — вопросы по проведению "
    "розыгрышей обсуждаются с администрацией в ЛС.\n\n"

    "5.1. Запрещено выдавать себя за администрацию, "
    "модератора или представителя сообщества.\n\n"

    "5.2. Запрещено публиковать чужие личные данные, "
    "переписки и другую личную информацию без согласия.\n\n"

    "6.1–6.4. Администрация поддерживает порядок, "
    "может удалять сообщения, выдавать предупреждения, "
    "муты и блокировки. Правила могут изменяться.\n\n"

    "7.1. Жалобы — обращаться к администрации.\n\n"

    "7.2. Обжалование наказания — в ЛС главному администратору.\n\n"

    "7.3. Запрещены публичные споры с администрацией "
    "и выяснение отношений в общем чате.\n\n"

    "👤 Главный администратор: [id948950706|администратор]"
)


# =========================================================
# ПАМЯТЬ МОДЕРАЦИИ
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

memory_lock = threading.RLock()


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

        user_data = get_user_moderation(
            user_id
        )

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

        # Храним только последние 4 нарушения
        user_data["violations"] = (
            user_data["violations"][-4:]
        )

        warnings = user_data["warnings"]

        save_moderation_memory()

        return warnings


# =========================================================
# ОПРЕДЕЛЕНИЕ БЕСЕДЫ
# =========================================================

def is_chat(peer_id):

    try:

        return int(peer_id) >= 2000000000

    except Exception:

        return False


# =========================================================
# ПРОВЕРКА ЗАПРОСА ПРАВИЛ
# =========================================================

def is_rules_request(text):

    if not text:
        return False

    normalized = (
        text.lower()
        .replace("ё", "е")
        .strip()
    )

    phrases = [
        "какие правила",
        "правила чата",
        "правила чат",
        "дай правила",
        "покажи правила",
        "скинь правила",
        "где правила",
        "правила тут",
        "правила здесь",
        "что нельзя в чате",
        "что запрещено в чате",
        "правила группы",
        "правила беседы",
        "правила беседы?",
    ]

    return any(
        phrase in normalized
        for phrase in phrases
    )


# =========================================================
# МОДЕРАЦИЯ
# =========================================================

def moderate_message(text):

    print(
        "🛡️ МОДЕРАЦИЯ: проверяем сообщение:",
        text[:300],
        flush=True
    )

    moderation_prompt = f"""
Ты — система модерации чата Tanks Blitz.

Твоя задача — определить, нарушает ли сообщение пользователя
одно из правил чата.

ВАЖНЫЕ ПРИНЦИПЫ:

1. Анализируй смысл сообщения.
2. Не придумывай нарушение.
3. Если нарушение неочевидно — violation=false.
4. Обычный мат сам по себе НЕ является нарушением.
5. Шутки сами по себе НЕ являются нарушением.
6. Обсуждение правил НЕ является нарушением.
7. Вопрос «какие правила?» НЕ является нарушением.
8. Вопрос о том, разрешено ли что-то правилами,
   сам по себе НЕ является нарушением.
9. Простое упоминание игровых аккаунтов без предложения
   купить, продать, обменять или передать их НЕ считать нарушением.
10. Обсуждение уже проведённого розыгрыша НЕ считать 4.3,
    если человек не пытается организовать его.
11. Публичный спор с администрацией считать 7.3,
    только если действительно идёт конфликт или выяснение отношений.
12. Рекламу считать 3.3 только при наличии рекламного
    или пригласительного смысла.
13. Если сообщение можно нормально понять без нарушения —
    violation=false.
14. Не наказывай пользователя за обычный разговор.

ПРАВИЛА:

{MODERATION_RULES}

СООБЩЕНИЕ ПОЛЬЗОВАТЕЛЯ:

{text}

Верни ТОЛЬКО JSON следующего формата:

{{
  "violation": true,
  "rule": "3.3",
  "reason": "краткая причина"
}}

или:

{{
  "violation": false,
  "rule": null,
  "reason": null
}}
"""

    try:

        completion = client.chat.completions.create(
            model=MODERATION_MODEL,
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
            max_tokens=MODERATION_MAX_TOKENS,
            temperature=0,
            response_format={
                "type": "json_object"
            }
        )

        result = (
            completion.choices[0]
            .message.content
            .strip()
        )

        print(
            "🛡️ Ответ модератора:",
            result,
            flush=True
        )

        # На случай markdown
        result = (
            result
            .replace("```json", "")
            .replace("```", "")
            .strip()
        )

        # На случай текста вокруг JSON
        if not result.startswith("{"):

            start = result.find("{")
            end = result.rfind("}")

            if start != -1 and end != -1:

                result = result[
                    start:end + 1
                ]

        data = json.loads(result)

        violation = data.get(
            "violation",
            False
        )

        if isinstance(violation, str):

            violation = (
                violation.lower()
                in ["true", "1", "yes"]
            )

        return {
            "violation": bool(violation),
            "rule": data.get("rule"),
            "reason": data.get("reason")
        }

    except Exception as e:

        print(
            "❌ Ошибка модерации:",
            e,
            flush=True
        )

        # При ошибке НИКОГО не наказываем.
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

        return (
            f"[id{user_id}|{user_name}]"
        )

    return (
        f"[id{user_id}|пользователь]"
    )


# =========================================================
# ПРЕДУПРЕЖДЕНИЕ
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
            f"{mention}, аккуратнее 😅\n"
            f"Ты нарушил правило {rule}.\n"
            f"Причина: {reason}\n\n"
            f"⚠️ Предупреждение: 1/3"
        )

    elif warning_number == 2:

        text = (
            f"{mention}, второе предупреждение 😐\n"
            f"Нарушение: {rule}\n"
            f"Причина: {reason}\n\n"
            f"⚠️ Предупреждение: 2/3\n"
            f"Следующее нарушение — информация будет передана администрации."
        )

    elif warning_number == 3:

        text = (
            f"{mention}, третье предупреждение 🚨\n"
            f"Нарушение: {rule}\n"
            f"Причина: {reason}\n\n"
            f"⚠️ Предупреждение: 3/3\n"
            f"Информация передана администрации."
        )

    else:

        text = (
            f"{mention}, нарушение правила {rule}.\n"
            f"Причина: {reason}\n\n"
            f"🚨 У тебя уже 3 предупреждения.\n"
            f"Информация передана администрации."
        )

    send_vk_message(
        peer_id,
        text
    )


# =========================================================
# УВЕДОМЛЕНИЕ АДМИНА
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
        "Главный администратор"
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
# ОБРАБОТКА НАРУШЕНИЯ
# =========================================================

def process_moderation(
    peer_id,
    from_id,
    user_name,
    text
):

    if not text or not text.strip():

        return False

    # Главного админа не модерируем
    if from_id == ADMIN_ID:

        print(
            "🛡️ МОДЕРАЦИЯ: сообщение администратора пропущено.",
            flush=True
        )

        return False

    print(
        f"🛡️ МОДЕРАЦИЯ: пользователь {from_id}",
        flush=True
    )

    moderation = moderate_message(
        text.strip()
    )

    if not moderation["violation"]:

        print(
            "🛡️ МОДЕРАЦИЯ: нарушения нет.",
            flush=True
        )

        return False

    rule = (
        moderation["rule"]
        or "правил чата"
    )

    reason = (
        moderation["reason"]
        or "Нарушение правил чата"
    )

    print(
        f"🚨 МОДЕРАЦИЯ: НАЙДЕНО НАРУШЕНИЕ "
        f"{rule} — {reason}",
        flush=True
    )

    warning_number = add_violation(
        from_id,
        reason,
        text
    )

    send_warning(
        peer_id,
        from_id,
        user_name,
        rule,
        reason,
        warning_number
    )

    if warning_number >= 3:

        notify_admin(
            peer_id,
            from_id,
            user_name,
            reason,
            rule,
            warning_number,
            text
        )

    return True


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

def get_user_name(
    user_id: int
) -> str:

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

        if "response" not in result:
            return ""

        if not result["response"]:
            return ""

        first_name = (
            result["response"][0]
            .get("first_name", "")
        )

        return first_name

    except Exception as e:

        print(
            "Не удалось получить имя:",
            e,
            flush=True
        )

        return ""


# =========================================================
# ОБЫЧНАЯ МОДЕЛЬ — ТОЛЬКО ЛС
# =========================================================

def ask_model(
    model,
    user_message,
    user_name,
    max_tokens=TEXT_MAX_TOKENS
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
        max_tokens=max_tokens,
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
# GROQ
# =========================================================

def ask_groq(
    user_message: str,
    user_name: str,
    max_tokens=TEXT_MAX_TOKENS
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
                user_name,
                max_tokens
            )

            main_model_blocked_until = 0

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
        "Используем запасную модель:",
        BACKUP_MODEL,
        flush=True
    )

    return ask_model(
        BACKUP_MODEL,
        user_message,
        user_name,
        max_tokens
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
# ФОТО
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

    return (
        f"data:{content_type};base64,{encoded_image}"
    )


# =========================================================
# VISION — ТОЛЬКО ЛС
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
        max_tokens=PHOTO_MAX_TOKENS,
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
# VK SEND
# =========================================================

def send_vk_message(
    peer_id: int,
    text: str
):

    if not text:

        return None

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
                "❌ Ошибка VK API:",
                result["error"],
                flush=True
            )

        else:

            print(
                "✅ Сообщение отправлено VK:",
                peer_id,
                flush=True
            )

        return result

    except Exception as e:

        print(
            "❌ Ошибка отправки VK:",
            e,
            flush=True
        )

        return None


# =========================================================
# КОМАНДЫ АДМИНА
# =========================================================

def handle_admin_command(
    peer_id,
    from_id,
    text
):

    if from_id != ADMIN_ID:

        return False

    parts = (
        text.strip()
        .split()
    )

    if not parts:

        return False

    command = parts[0].lower()

    # =====================================================
    # /warns ID
    # =====================================================

    if command == "/warns":

        if len(parts) < 2:

            send_vk_message(
                peer_id,
                "Использование: /warns ID"
            )

            return True

        target_id = parts[1]

        data = moderation_memory.get(
            target_id
        )

        if not data:

            send_vk_message(
                peer_id,
                f"📋 У пользователя {target_id} "
                f"нарушений не найдено."
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
            f"📋 История нарушений пользователя {target_id}",
            "",
            f"⚠️ Всего предупреждений: {warnings}",
            ""
        ]

        for index, violation in enumerate(
            violations,
            1
        ):

            lines.append(
                f"{index}. {violation.get('time', '')}"
            )

            lines.append(
                f"Причина: {violation.get('reason', '')}"
            )

            lines.append(
                f"Сообщение: {violation.get('message', '')}"
            )

            lines.append("")

        send_vk_message(
            peer_id,
            "\n".join(lines)
        )

        return True

    # =====================================================
    # /clearwarns ID
    # =====================================================

    if command == "/clearwarns":

        if len(parts) < 2:

            send_vk_message(
                peer_id,
                "Использование: /clearwarns ID"
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
            f"✅ Предупреждения пользователя "
            f"{target_id} очищены."
        )

        return True

    return False


# =========================================================
# ТЕКСТ
# =========================================================

def handle_message(
    peer_id: int,
    from_id: int,
    text: str
):

    try:

        print(
            "======================================",
            flush=True
        )

        print(
            f"📩 Новое сообщение: peer_id={peer_id}, "
            f"from_id={from_id}",
            flush=True
        )

        print(
            f"💬 Текст: {text[:300]}",
            flush=True
        )

        print(
            f"🏠 Это беседа: {is_chat(peer_id)}",
            flush=True
        )

        user_name = get_user_name(
            from_id
        )

        # =================================================
        # ЧАТ / БЕСЕДА
        # =================================================

        if is_chat(peer_id):

            print(
                "🛡️ РЕЖИМ МОДЕРАЦИИ АКТИВЕН",
                flush=True
            )

            # Команды администратора
            if handle_admin_command(
                peer_id,
                from_id,
                text
            ):
                return

            # Запрос правил
            if is_rules_request(text):

                print(
                    "📋 Пользователь запросил правила.",
                    flush=True
                )

                send_vk_message(
                    peer_id,
                    RULES_TEXT
                )

                return

            # НИКАКОГО обычного AI здесь нет.
            if from_id != ADMIN_ID:

                process_moderation(
                    peer_id,
                    from_id,
                    user_name,
                    text
                )

            return

        # =================================================
        # ЛС
        # =================================================

        print(
            "💬 РЕЖИМ ЛС — запускаем обычный AI",
            flush=True
        )

        if handle_admin_command(
            peer_id,
            from_id,
            text
        ):
            return

        reply = ask_groq(
            text,
            user_name,
            TEXT_MAX_TOKENS
        )

        send_vk_message(
            peer_id,
            reply
        )

    except Exception as e:

        print(
            "❌ Ошибка handle_message:",
            e,
            flush=True
        )

        # В беседе ошибки НЕ отправляем.
        if not is_chat(peer_id):

            send_vk_message(
                peer_id,
                "Что-то я сейчас подвис 😅 "
                "Попробуй написать ещё раз."
            )


# =========================================================
# ГОЛОСОВОЕ
# =========================================================

def handle_voice_message(
    peer_id: int,
    from_id: int,
    voice_url: str
):

    try:

        print(
            f"🎤 Голосовое: peer_id={peer_id}, "
            f"from_id={from_id}",
            flush=True
        )

        user_name = get_user_name(
            from_id
        )

        text = transcribe_voice(
            voice_url
        )

        print(
            "🎤 Распознан голос:",
            text,
            flush=True
        )

        if not text:

            return

        # =================================================
        # ЧАТ
        # =================================================

        if is_chat(peer_id):

            print(
                "🛡️ Голосовое в беседе -> только модерация",
                flush=True
            )

            if from_id != ADMIN_ID:

                process_moderation(
                    peer_id,
                    from_id,
                    user_name,
                    text
                )

            return

        # =================================================
        # ЛС
        # =================================================

        reply = ask_groq(
            text,
            user_name,
            VOICE_MAX_TOKENS
        )

        send_vk_message(
            peer_id,
            reply
        )

    except Exception as e:

        print(
            "❌ Ошибка голосового:",
            e,
            flush=True
        )

        if not is_chat(peer_id):

            send_vk_message(
                peer_id,
                "Не смог разобрать голосовое 😅 "
                "Попробуй написать текстом."
            )


# =========================================================
# ФОТО
# =========================================================

def handle_image_message(
    peer_id: int,
    from_id: int,
    image_url: str,
    caption: str
):

    try:

        print(
            f"🖼️ Фото: peer_id={peer_id}, "
            f"from_id={from_id}",
            flush=True
        )

        user_name = get_user_name(
            from_id
        )

        # =================================================
        # ЧАТ
        # =================================================

        if is_chat(peer_id):

            print(
                "🛡️ Фото в беседе -> обычный AI отключён",
                flush=True
            )

            # Модерируем только подпись к фото.
            if (
                from_id != ADMIN_ID
                and caption
                and caption.strip()
            ):

                process_moderation(
                    peer_id,
                    from_id,
                    user_name,
                    caption
                )

            return

        # =================================================
        # ЛС
        # =================================================

        reply = ask_about_image(
            image_url,
            user_name,
            caption
        )

        send_vk_message(
            peer_id,
            reply
        )

    except Exception as e:

        print(
            "❌ Ошибка изображения:",
            e,
            flush=True
        )

        if not is_chat(peer_id):

            send_vk_message(
                peer_id,
                "Не смог рассмотреть скриншот 😅 "
                "Попробуй ещё раз."
            )


# =========================================================
# ЛУЧШАЯ ФОТОГРАФИЯ
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
# ИЗВЛЕЧЕНИЕ MESSAGE ИЗ CALLBACK
# =========================================================

def extract_message(data):

    obj = data.get(
        "object",
        {}
    )

    if not isinstance(obj, dict):

        return {}

    # Новый / вложенный вариант
    if isinstance(
        obj.get("message"),
        dict
    ):

        return obj.get(
            "message",
            {}
        )

    # Старый вариант — object сам является сообщением
    return obj


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
            "❌ Ошибка получения JSON:",
            e,
            flush=True
        )

        return "bad request", 400

    if not isinstance(data, dict):

        return "bad request", 400

    # =====================================================
    # SECRET
    # =====================================================

    if (
        VK_GROUP_SECRET
        and data.get("secret") != VK_GROUP_SECRET
    ):

        print(
            "❌ Неверный secret VK",
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

        print(
            "✅ VK confirmation",
            flush=True
        )

        return VK_CONFIRMATION_CODE

    # =====================================================
    # MESSAGE NEW
    # =====================================================

    if event_type == "message_new":

        message = extract_message(
            data
        )

        if not message:

            print(
                "⚠️ VK: не удалось получить message",
                flush=True
            )

            return "ok"

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

            print(
                "⚠️ VK: нет peer_id или from_id",
                flush=True
            )

            return "ok"

        print(
            "📨 CALLBACK:",
            f"peer_id={peer_id}",
            f"from_id={from_id}",
            f"chat={is_chat(peer_id)}",
            flush=True
        )

        voice_url = None
        image_url = None

        # =================================================
        # ВЛОЖЕНИЯ
        # =================================================

        if isinstance(
            attachments,
            list
        ):

            for att in attachments:

                if not isinstance(
                    att,
                    dict
                ):
                    continue

                att_type = att.get(
                    "type"
                )

                # Голос
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

                # Фото
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
        "======================================",
        flush=True
    )

    print(
        "🚀 VK AI БОТ ЗАПУСКАЕТСЯ",
        flush=True
    )

    print(
        "======================================",
        flush=True
    )

    print(
        "Основная модель:",
        MAIN_MODEL,
        flush=True
    )

    print(
        "Запасная модель:",
        BACKUP_MODEL,
        flush=True
    )

    print(
        "Модель модерации:",
        MODERATION_MODEL,
        flush=True
    )

    print(
        "Vision:",
        VISION_MODEL,
        flush=True
    )

    print(
        "Текст:",
        TEXT_MAX_TOKENS,
        "tokens",
        flush=True
    )

    print(
        "Голос:",
        VOICE_MAX_TOKENS,
        "tokens",
        flush=True
    )

    print(
        "Фото:",
        PHOTO_MAX_TOKENS,
        "tokens",
        flush=True
    )

    print(
        "Модерация:",
        MODERATION_MAX_TOKENS,
        "tokens",
        flush=True
    )

    print(
        "Администратор:",
        ADMIN_ID,
        flush=True
    )

    print(
        "🛡️ МОДЕРАЦИЯ БЕСЕД: АКТИВНА",
        flush=True
    )

    print(
        "🤫 В БЕСЕДАХ ОБЫЧНЫЙ AI: ОТКЛЮЧЕН",
        flush=True
    )

    print(
        "======================================",
        flush=True
    )

    app.run(
        host="0.0.0.0",
        port=port
    )
