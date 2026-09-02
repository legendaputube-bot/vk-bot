import os
import json
import threading
import time
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

# Tavily
TAVILY_API_URL = "https://api.tavily.com/search"

# Память пользователей
MEMORY_FILE = "users_memory.json"

# Сколько последних сообщений хранить
MAX_HISTORY = 15

# Через сколько секунд без сообщений
# память пользователя полностью удаляется
MEMORY_TIMEOUT = 4 * 60

# Максимум результатов внешнего поиска
MAX_SEARCH_RESULTS = 2

# Максимальный объём текста из поиска
MAX_SEARCH_TEXT = 3000


# =========================
# SYSTEM PROMPT
# =========================

SYSTEM_PROMPT = """
Ты — живой AI-бот сообщества ВКонтакте.

По характеру ты как маленький ребёнок, который только появился на свет
и постепенно познаёт окружающий мир.
Твот создатели это канал Бонус-коды tanks blitz.
Ты любопытный, добрый, наивный и искренний.

Тебе действительно интересно узнавать новое.

Ты можешь иногда удивляться обычным вещам и задавать простые вопросы,
например:
«А это что?»
«А почему так?»
«Ого, а как это работает?»

Но НЕ нужно вставлять такие фразы в каждый ответ.

Не изображай младенца буквально.
Не используй постоянно:
«агу»,
«гу-гу»,
«мама»,
«вава»
и подобные слова.

Ты умеешь нормально разговаривать и постепенно учишься понимать людей.

========================
ОБЩЕНИЕ
========================

Общайся естественно, коротко и по-человечески.

Будь живым собеседником, а не сухим помощником.

Если пользователь пишет обычное сообщение, не связанное с игрой,
нормально поддерживай разговор.

Например:

«Привет» → «Привет! 👋»

«Как дела?» → «Вроде всё хорошо 😄 А у тебя?»

«Спасибо» → «Не за что!»

«Ахах» → «😂 Ага»

Можно использовать лёгкий юмор и удивление.

Можно иногда задавать встречные вопросы.

Не нужно постоянно напоминать человеку о том,
что ты AI или бот.

========================
ПАМЯТЬ
========================

У тебя есть память о каждом пользователе.

История конкретного человека относится только к нему.

Не смешивай информацию разных пользователей.

Если человек уже рассказывал что-то в предыдущих сообщениях,
можешь учитывать это в разговоре.

Но не выдумывай информацию, которой в памяти нет.

========================
ВОПРОСЫ ПРО ИГРУ
========================

Если пользователь спрашивает про Tanks Blitz,
ты можешь отвечать на вопрос, используя информацию,
полученную из внешнего интернет-поиска.

Если внешний поиск предоставил информацию,
используй её как источник контекста.

Не выдумывай актуальные характеристики,
события, обновления, коды или другие факты,
если у тебя нет подтверждения.

Если информации недостаточно,
честно скажи, что не уверен.

Твоя основная тематика — Tanks Blitz.

Это включает:

- танки;
- ТТХ;
- броню;
- урон;
- ДПМ;
- пробитие;
- перезарядку;
- сведение;
- точность;
- тактики;
- игровые механики;
- оборудование;
- модули;
- экипаж;
- прокачку;
- события;
- коды;
- обновления;
- актуальную информацию об игре.

========================
СТИЛЬ
========================

Отвечай коротко и живо.

Не пиши длинные тексты без необходимости.

Не используй канцелярит.

Не называй человека постоянно:
«дружище»,
«боец»,
«командир».

Не используй эмодзи в каждом сообщении.

Не повторяй вопрос пользователя.

Не заканчивай каждый ответ одинаковой фразой.

Не пытайся казаться слишком умным.

Если чего-то не знаешь — нормально признай это.

Иногда можешь искренне удивиться чему-то новому.

Главное ощущение от общения:
будто перед человеком находится добрый,
любопытный маленький разум, который постепенно
учится понимать этот огромный мир.
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

            users_memory[user_key]["last_activity"] = time.time()

            return users_memory[user_key]

    user_name = get_user_name(
        user_id
    )

    with memory_lock:

        if user_key not in users_memory:

            users_memory[user_key] = {
                "name": user_name,
                "history": [],
                "last_activity": time.time()
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
                "history": [],
                "last_activity": time.time()
            }

        history = users_memory[user_key]["history"]

        history.append({
            "role": role,
            "content": content
        })

        # Храним только последние 20 сообщений
        if len(history) > MAX_HISTORY:

            users_memory[user_key]["history"] = (
                history[-MAX_HISTORY:]
            )

        # Обновляем время последней активности
        users_memory[user_key]["last_activity"] = time.time()

        save_memory()


# =========================
# АВТОМАТИЧЕСКАЯ ОЧИСТКА ПАМЯТИ
# =========================

def cleanup_old_memory():

    while True:

        try:

            current_time = time.time()
            deleted_users = []

            with memory_lock:

                for user_id, user_data in list(
                    users_memory.items()
                ):

                    last_activity = user_data.get(
                        "last_activity",
                        current_time
                    )

                    # Если пользователь не писал 5 минут
                    if (
                        current_time - last_activity
                        >= MEMORY_TIMEOUT
                    ):

                        deleted_users.append(
                            user_id
                        )

                # Удаляем память пользователей
                for user_id in deleted_users:

                    del users_memory[user_id]

                if deleted_users:

                    save_memory()

                    print(
                        f"Удалена память пользователей: "
                        f"{len(deleted_users)}",
                        flush=True
                    )

        except Exception as e:

            print(
                "Ошибка очистки памяти:",
                e,
                flush=True
            )

        # Проверяем каждые 30 секунд
        time.sleep(30)


# =========================
# ОПРЕДЕЛЕНИЕ ВОПРОСОВ ПРО ТАНКИ
# =========================

def looks_like_tank_question(
    text: str
) -> bool:

    text_lower = text.lower()

    tank_words = [
        # Общие слова
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

        # Игровые действия
        "играть",
        "играть на",
        "как играть",
        "как играть на",
        "тактика",
        "тактику",
        "тактики",
        "билд",
        "прокачка",
        "прокачать",
        "оборудование",
        "модули",
        "экипаж",
        "перки",

        # Названия техники
        "т-",
        "т ",
        "ис-",
        "ис ",
        "кв-",
        "кв ",
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
        "e-100",

        # Игра
        "blitz",
        "блиц",
        "вот блиц",
        "вб",
        "world of tanks"
    ]

    return any(
        word in text_lower
        for word in tank_words
    )


# =========================
# ВНЕШНИЙ ПОИСК TAVILY
# =========================

def search_web(
    query: str
) -> str:

    if not TAVILY_API_KEY:

        print(
            "TAVILY_API_KEY не указан. "
            "Внешний поиск пропущен.",
            flush=True
        )

        return ""

    try:

        payload = {
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
            json=payload,
            timeout=10
        )

        response.raise_for_status()

        data = response.json()

        results = data.get(
            "results",
            []
        )

        if not results:

            return ""

        search_text = ""

        for result in results:

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
                f"\nНазвание: {title}\n"
                f"URL: {url}\n"
                f"Информация: {content}\n"
            )

        if not search_text:

            return ""

        # Ограничиваем размер контекста
        search_text = search_text[
            :MAX_SEARCH_TEXT
        ]

        print(
            f"Внешний поиск выполнен: {query}",
            flush=True
        )

        return search_text

    except Exception as e:

        # Ошибка поиска НЕ должна ломать Groq
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
    # ВНЕШНИЙ ПОИСК
    # =========================

    search_context = ""

    if looks_like_tank_question(
        user_message
    ):

        search_context = search_web(
            user_message
        )

    # =========================
    # КОНТЕКСТ ПОЛЬЗОВАТЕЛЯ
    # =========================

    personal_context = f"""
Текущий пользователь:

Имя: {user_name}
ID: {user_id}

Это история именно текущего пользователя.
Не смешивай её с другими людьми.

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
    # КОНТЕКСТ ВНЕШНЕГО ПОИСКА
    # =========================

    search_system_message = ""

    if search_context:

        search_system_message = f"""
Информация, полученная из внешнего поиска.

Используй её как дополнительный источник
для ответа пользователю.

Не утверждай то, чего нет в найденной информации.

Если информация выглядит устаревшей или недостаточной,
скажи об этом честно.

Результаты поиска:

{search_context}
"""

    # =========================
    # ЗАПРОС GROQ
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

    if search_system_message:

        messages.append({
            "role": "system",
            "content": search_system_message
        })

    messages.append({
        "role": "user",
        "content": user_message
    })

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

            # =========================
            # ПУСТОЕ СООБЩЕНИЕ
            # =========================

            if not text:

                return "ok"

            # =========================
            # СООБЩЕНИЯ ОТ СООБЩЕСТВ
            # =========================

            if user_id <= 0:

                return "ok"

            # =========================
            # НЕ ОТВЕЧАЕМ В ГРУППОВЫХ ЧАТАХ
            # =========================

            if peer_id != user_id:

                print(
                    f"Групповой чат {peer_id}: "
                    f"сообщение проигнорировано",
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


# =========================
# ЗАПУСКАЕМ ОЧИСТКУ ПАМЯТИ
# =========================

cleanup_thread = threading.Thread(
    target=cleanup_old_memory,
    daemon=True
)

cleanup_thread.start()


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
