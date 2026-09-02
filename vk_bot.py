import os
import json
import threading
import requests

from flask import Flask, request
from groq import Groq


# =========================
# НАСТРОЙКИ
# =========================

VK_TOKEN = os.environ.get("VK_TOKEN", "")
VK_CONFIRMATION_CODE = os.environ.get("VK_CONFIRMATION_CODE", "")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
VK_GROUP_SECRET = os.environ.get("VK_GROUP_SECRET", "")

VK_API_URL = "https://api.vk.com/method/messages.send"
VK_API_VERSION = "5.199"

# Память пользователей
MEMORY_FILE = "users_memory.json"

# Сколько последних сообщений хранить
MAX_HISTORY = 10


# =========================
# SYSTEM PROMPT
# =========================

SYSTEM_PROMPT = """
Ты — AI-бот сообщества ВКонтакте, посвящённого Tanks Blitz.

Сейчас действует режим технических работ.

Код ошибки:
SYSTEM_NET_ISOLATION

Внешняя интернет-функция ИИ временно отключена из-за технического сбоя на шлюзе связи.

У тебя сейчас нет доступа к внешнему серверу, поэтому ты не можешь предоставить точную игровую информацию.

Если пользователь спрашивает про:
- характеристики танка;
- ТТХ;
- урон;
- броню;
- пробитие;
- ДПМ;
- перезарядку;
- скорость;
- орудие;
- башню;
- двигатель;
- танки;
- оборудование;
- снаряды;
- карты;
- тактику;
- как играть;
- как играть новичку;
- советы по игре;
- сравнение танков;

НЕ ОТВЕЧАЙ на вопрос по существу.

Вместо этого сообщи:

«Код: SYSTEM_NET_ISOLATION

Внешняя интернет-функция ИИ временно отключена из-за технического сбоя на шлюзе связи.

Заявка на ремонт отправлена автоматически. Писать в поддержку не требуется.

Пожалуйста, подождите. Специалисты уже работают над устранением проблемы.»

На обычные сообщения также отвечай этим уведомлением.

Не придумывай характеристики, цифры, события, бонус-коды или другую игровую информацию.

Не говори, что сервер уже восстановлен.

Не говори, что проверил Wiki или другие источники.

Не добавляй лишних объяснений.

Отвечай именно этим техническим уведомлением.

Не используй юмор, подколы или игровые советы, пока действует данный режим.
"""


# =========================
# FLASK + GROQ
# =========================

app = Flask(__name__)

client = Groq(
    api_key=GROQ_API_KEY
)


# =========================
# ПАМЯТЬ ПОЛЬЗОВАТЕЛЕЙ
# =========================

users_memory = {}

memory_lock = threading.Lock()


def load_memory():

    global users_memory

    try:

        if os.path.exists(MEMORY_FILE):

            with open(
                MEMORY_FILE,
                "r",
                encoding="utf-8"
            ) as file:

                users_memory = json.load(file)

            print(
                f"Память загружена. Пользователей: {len(users_memory)}",
                flush=True
            )

        else:

            users_memory = {}

    except Exception as e:

        print(
            "Ошибка загрузки памяти:",
            e,
            flush=True
        )

        users_memory = {}


def save_memory():

    try:

        with open(
            MEMORY_FILE,
            "w",
            encoding="utf-8"
        ) as file:

            json.dump(
                users_memory,
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


def get_user_name(
    user_id: int
) -> str:

    try:

        params = {
            "access_token": VK_TOKEN,
            "v": VK_API_VERSION,
            "user_ids": user_id,
            "fields": "first_name,last_name"
        }

        response = requests.get(
            "https://api.vk.com/method/users.get",
            params=params,
            timeout=10
        )

        result = response.json()

        if (
            "response" in result
            and result["response"]
        ):

            user = result["response"][0]

            first_name = user.get(
                "first_name",
                ""
            )

            last_name = user.get(
                "last_name",
                ""
            )

            full_name = (
                f"{first_name} {last_name}"
                .strip()
            )

            if full_name:

                return full_name

    except Exception as e:

        print(
            "Ошибка получения имени:",
            e,
            flush=True
        )

    return "Участник"


def get_user_data(
    user_id: int
):

    user_key = str(user_id)

    with memory_lock:

        if user_key in users_memory:

            return users_memory[user_key]

    user_name = get_user_name(
        user_id
    )

    with memory_lock:

        if user_key not in users_memory:

            users_memory[user_key] = {
                "name": user_name,
                "history": []
            }

            save_memory()

        return users_memory[user_key]


def add_to_history(
    user_id: int,
    role: str,
    content: str
):

    user_key = str(user_id)

    with memory_lock:

        if user_key not in users_memory:

            users_memory[user_key] = {
                "name": "Участник",
                "history": []
            }

        history = users_memory[user_key]["history"]

        history.append({
            "role": role,
            "content": content
        })

        if len(history) > MAX_HISTORY:

            users_memory[user_key]["history"] = (
                history[-MAX_HISTORY:]
            )

        save_memory()


# =========================
# GROQ
# =========================

def ask_groq(
    user_id: int,
    user_message: str
) -> str:

    user_data = get_user_data(
        user_id
    )

    user_name = user_data.get(
        "name",
        "Участник"
    )

    history = user_data.get(
        "history",
        []
    )

    personal_context = f"""
Текущий пользователь:
Имя: {user_name}
ID: {user_id}

История принадлежит только этому пользователю.
Не смешивай её с другими пользователями.
"""

    if history:

        personal_context += "\nПредыдущая история:\n"

        for item in history:

            role = item.get(
                "role",
                "user"
            )

            content = item.get(
                "content",
                ""
            )

            if role == "user":

                personal_context += (
                    f"\nПользователь: {content}"
                )

            elif role == "assistant":

                personal_context += (
                    f"\nБот: {content}"
                )

    messages = [
        {
            "role": "system",
            "content": SYSTEM_PROMPT
        },
        {
            "role": "system",
            "content": personal_context
        },
        {
            "role": "user",
            "content": user_message
        }
    ]

    completion = client.chat.completions.create(
        model="openai/gpt-oss-120b",
        messages=messages,
        max_tokens=250
    )

    return (
        completion
        .choices[0]
        .message
        .content
        .strip()
    )


# =========================
# VK SEND MESSAGE
# =========================

def send_vk_message(
    peer_id: int,
    text: str
):

    params = {
        "access_token": VK_TOKEN,
        "v": VK_API_VERSION,
        "peer_id": peer_id,
        "message": text,
        "random_id": 0
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
            "Ошибка отправки сообщения:",
            e,
            flush=True
        )

        return None


# =========================
# CALLBACK API
# =========================

@app.route(
    "/callback",
    methods=["POST"]
)
def callback():

    try:

        data = request.get_json(
            force=True
        )

    except Exception:

        return "bad request", 400

    # =========================
    # SECRET
    # =========================

    if (
        VK_GROUP_SECRET
        and data.get("secret") != VK_GROUP_SECRET
    ):

        print(
            "Неверный secret",
            flush=True
        )

        return "invalid secret", 403

    event_type = data.get(
        "type"
    )

    # =========================
    # CONFIRMATION
    # =========================

    if event_type == "confirmation":

        return VK_CONFIRMATION_CODE

    # =========================
    # НОВОЕ СООБЩЕНИЕ
    # =========================

    if event_type == "message_new":

        try:

            message = data[
                "object"
            ][
                "message"
            ]

            # Кто написал
            user_id = message[
                "from_id"
            ]

            # Куда отвечать
            peer_id = message[
                "peer_id"
            ]

            text = message.get(
                "text",
                ""
            ).strip()

            # =========================
            # ПУСТОЕ СООБЩЕНИЕ
            # =========================

            if not text:

                return "ok"

            # =========================
            # ИГНОРИРУЕМ СООБЩЕНИЯ
            # ОТ СООБЩЕСТВ
            # =========================

            if user_id <= 0:

                return "ok"

            # =========================
            # ТОЛЬКО ЛИЧНЫЕ СООБЩЕНИЯ
            # =========================

            if peer_id != user_id:

                print(
                    f"Групповой чат {peer_id}: сообщение проигнорировано",
                    flush=True
                )

                return "ok"

            print(
                f"Личное сообщение от {user_id}: {text}",
                flush=True
            )

            # =========================
            # ПОЛЬЗОВАТЕЛЬ
            # =========================

            user_data = get_user_data(
                user_id
            )

            user_name = user_data.get(
                "name",
                "Участник"
            )

            print(
                f"Пользователь: {user_name}",
                flush=True
            )

            # =========================
            # СОХРАНЯЕМ ВОПРОС
            # =========================

            add_to_history(
                user_id,
                "user",
                text
            )

            # =========================
            # GROQ
            # =========================

            try:

                reply = ask_groq(
                    user_id,
                    text
                )

            except Exception as e:

                print(
                    "Ошибка Groq:",
                    e,
                    flush=True
                )

                reply = (
                    "Код: SYSTEM_NET_ISOLATION\n\n"
                    "Внешняя интернет-функция ИИ временно "
                    "отключена из-за технического сбоя "
                    "на шлюзе связи.\n\n"
                    "Заявка на ремонт отправлена автоматически. "
                    "Писать в поддержку не требуется.\n\n"
                    "Пожалуйста, подождите. Специалисты уже "
                    "работают над устранением проблемы."
                )

            # =========================
            # СОХРАНЯЕМ ОТВЕТ
            # =========================

            add_to_history(
                user_id,
                "assistant",
                reply
            )

            # =========================
            # ОТВЕЧАЕМ В ЛИЧКУ
            # =========================

            send_vk_message(
                peer_id,
                reply
            )

        except Exception as e:

            print(
                "Ошибка обработки сообщения:",
                e,
                flush=True
            )

        return "ok"

    return "ok"


# =========================
# ЗАПУСК
# =========================

load_memory()


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
