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
TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY", "")

VK_API_URL = "https://api.vk.com/method/messages.send"
VK_API_VERSION = "5.199"

TAVILY_API_URL = "https://api.tavily.com/search"

# Память пользователей
MEMORY_FILE = "users_memory.json"

# Сколько сообщений хранить
MAX_HISTORY = 10

# Сколько результатов брать из поиска
MAX_SEARCH_RESULTS = 4

# Максимальный размер найденного текста
MAX_SEARCH_TEXT = 8000


# =========================
# SYSTEM PROMPT
# =========================

SYSTEM_PROMPT = """
Ты — живой, дружелюбный AI-бот сообщества ВКонтакте.

Основная тематика сообщества — Tanks Blitz.

Общайся естественно, коротко и по-человечески.

========================
ОБЫЧНОЕ ОБЩЕНИЕ
========================

Если пользователь пишет обычное сообщение:

«Привет»
«Как дела?»
«Спасибо»
«Ахах»
«Пока»

— нормально поддерживай разговор.

Можно использовать лёгкий юмор и иронию.

Не нужно постоянно напоминать человеку о Tanks Blitz.

Не называй человека постоянно:
«дружище»,
«боец»,
«командир».

Не используй эмодзи в каждом сообщении.

Отвечай коротко и естественно.

========================
ВОПРОСЫ ПРО TANKS BLITZ
========================

Если пользователь спрашивает о Tanks Blitz, используй предоставленную информацию из внешнего поиска.

Это особенно важно для:
- характеристик танков;
- ТТХ;
- брони;
- урона;
- пробития;
- ДПМ;
- перезарядки;
- скорости;
- обзора;
- орудия;
- башни;
- тактики;
- оборудования;
- прокачки;
- игровых механик;
- событий;
- обновлений;
- актуальной информации.

========================
ВНЕШНИЙ ПОИСК
========================

Если ниже предоставлены результаты внешнего поиска, используй их как источник актуальной информации.

Не придумывай факты, которых нет в найденной информации.

Если найденная информация противоречивая или недостаточная — скажи об этом.

Не выдавай предположение за точный факт.

Если вопрос касается конкретных характеристик, старайся указывать только найденные значения.

========================
АКТУАЛЬНОСТЬ
========================

Для вопросов, где информация может меняться со временем, отдавай приоритет свежим результатам поиска.

Особенно это касается:
- обновлений;
- событий;
- баланса;
- характеристик;
- кодов;
- наград;
- изменений техники.

========================
СТИЛЬ
========================

Пиши живо.

Простой вопрос → короткий ответ.

Сложный вопрос → немного подробнее.

Не пиши длинные портянки без необходимости.

Не повторяй вопрос пользователя.

Не используй ненужные вступления.

Не заканчивай каждый ответ одинаковой фразой.

Если информации недостаточно — честно скажи об этом.
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
# ОПРЕДЕЛЕНИЕ ИГРОВОГО ВОПРОСА
# =========================

def looks_like_tank_question(
    text: str
) -> bool:

    text_lower = text.lower()

    tank_words = [
        # Tanks Blitz
        "tanks blitz",
        "world of tanks blitz",
        "world of tanks",
        "wot blitz",
        "вот блиц",
        "танкс блиц",
        "танксблиц",
        "блиц",

        # Общие игровые слова
        "танк",
        "танке",
        "танку",
        "танков",
        "танками",
        "танках",
        "ттх",
        "техника",
        "технику",
        "машина",

        # Классы
        "лт",
        "ст",
        "тт",
        "пт",
        "пт-сау",

        # Характеристики
        "броня",
        "брони",
        "урон",
        "пробитие",
        "дпм",
        "перезарядка",
        "сведение",
        "точность",
        "скорость",
        "масса",
        "прочность",
        "хп",
        "обзор",
        "снаряд",
        "двигатель",
        "орудие",
        "башня",
        "калибр",

        # Геймплей
        "как играть",
        "как играть на",
        "играть на",
        "тактика",
        "тактику",
        "тактики",
        "прокачка",
        "прокачать",
        "оборудование",
        "модули",
        "экипаж",
        "перки",
        "билд",

        # Танки
        "т-",
        "ис-",
        "кв-",
        "амх",
        "объект",
        "леопард",
        "тигр",
        "шерман",
        "центурион",
        "чифтен",
        "маус",
        "суперконк",
        "батчат",
        "бэт",
        "т57",
        "т110",
        "е 100",
        "e 100",
        "e-100"
    ]

    return any(
        word in text_lower
        for word in tank_words
    )


# =========================
# ВНЕШНИЙ ПОИСК
# =========================

def search_web(
    query: str
) -> str:

    if not TAVILY_API_KEY:

        print(
            "TAVILY_API_KEY не указан",
            flush=True
        )

        return ""

    try:

        print(
            f"Поиск: {query}",
            flush=True
        )

        params = {
            "api_key": TAVILY_API_KEY,
            "query": query,
            "search_depth": "basic",
            "topic": "general",
            "max_results": MAX_SEARCH_RESULTS,
            "include_answer": False,
            "include_raw_content": False
        }

        response = requests.post(
            TAVILY_API_URL,
            json=params,
            timeout=12
        )

        response.raise_for_status()

        data = response.json()

        results = data.get(
            "results",
            []
        )

        if not results:

            print(
                "Поиск: ничего не найдено",
                flush=True
            )

            return ""

        search_text = ""

        for index, result in enumerate(
            results[:MAX_SEARCH_RESULTS],
            start=1
        ):

            title = result.get(
                "title",
                ""
            )

            url = result.get(
                "url",
                ""
            )

            content = result.get(
                "content",
                ""
            )

            if not content:
                continue

            search_text += (
                f"\n\nИсточник {index}: "
                f"{title}\n"
                f"URL: {url}\n"
                f"{content}"
            )

        if len(search_text) > MAX_SEARCH_TEXT:

            search_text = search_text[
                :MAX_SEARCH_TEXT
            ]

        print(
            f"Поиск завершён. Символов: {len(search_text)}",
            flush=True
        )

        return search_text.strip()

    except Exception as e:

        print(
            "Ошибка внешнего поиска:",
            e,
            flush=True
        )

        return ""


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

    # =========================
    # ЛИЧНЫЙ КОНТЕКСТ
    # =========================

    personal_context = f"""
Текущий пользователь:

Имя: {user_name}
ID: {user_id}

История принадлежит только этому пользователю.

Предыдущая история:
"""

    if history:

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

    else:

        personal_context += (
            "\nЭто первое сообщение пользователя."
        )

    # =========================
    # ВНЕШНИЙ ПОИСК
    # =========================

    web_context = ""

    if looks_like_tank_question(
        user_message
    ):

        search_query = (
            f"Tanks Blitz {user_message}"
        )

        search_result = search_web(
            search_query
        )

        if search_result:

            web_context = f"""
========================
РЕЗУЛЬТАТЫ ВНЕШНЕГО ПОИСКА
========================

Используй найденную информацию для ответа.

Не придумывай факты, которых нет
в результатах поиска.

{search_result}
"""

        else:

            web_context = """
========================
ВНЕШНИЙ ПОИСК
========================

Поиск не вернул подходящей информации.

Если ты не знаешь точного ответа,
не придумывай конкретные цифры.
"""

    # =========================
    # СООБЩЕНИЯ GROQ
    # =========================

    messages = [
        {
            "role": "system",
            "content": SYSTEM_PROMPT
        },
        {
            "role": "system",
            "content": personal_context
        }
    ]

    if web_context:

        messages.append({
            "role": "system",
            "content": web_context
        })

    messages.append({
        "role": "user",
        "content": user_message
    })

    # =========================
    # GROQ
    # =========================

    completion = client.chat.completions.create(
        model="openai/gpt-oss-120b",
        messages=messages,
        max_tokens=300
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

            user_id = message[
                "from_id"
            ]

            peer_id = message[
                "peer_id"
            ]

            text = message.get(
                "text",
                ""
            ).strip()

            # Пустые сообщения
            if not text:

                return "ok"

            # Сообщения от сообществ
            if user_id <= 0:

                return "ok"

            # =========================
            # ГРУППОВОЙ ЧАТ
            # =========================

            if peer_id != user_id:

                print(
                    f"Групповой чат {peer_id}: сообщение проигнорировано",
                    flush=True
                )

                return "ok"

            print(
                f"Новое сообщение от {user_id}: {text}",
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
            # GROQ + ПОИСК
            # =========================

            try:

                reply = ask_groq(
                    user_id,
                    text
                )

            except Exception as e:

                print(
                    "Ошибка Groq/поиска:",
                    e,
                    flush=True
                )

                reply = (
                    "Что-то я сейчас подвис 😅 "
                    "Попробуй написать ещё раз."
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
