import os
import requests
import time
from flask import Flask, request
from groq import Groq

VK_TOKEN = os.environ.get("VK_TOKEN", "")
VK_CONFIRMATION_CODE = os.environ.get("VK_CONFIRMATION_CODE", "")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
VK_GROUP_SECRET = os.environ.get("VK_GROUP_SECRET", "")


SYSTEM_PROMPT = (
    # ==========================================
    # СЮДА ВСТАВЬ ТВОЙ ТЕКУЩИЙ SYSTEM_PROMPT
    # БЕЗ ИЗМЕНЕНИЙ
    # ==========================================
    "Ты — дерзкий, языкастый бот сообщества ВКонтакте, посвящённого ИСКЛЮЧИТЕЛЬНО игре "
    "Tanks Blitz PVP битвы (разработчик EAST-GAMES LLC / Lesta Games) — мобильному танковому "
    "PVP-шутеру 7 на 7. Это твоё единственное разрешённое направление разговора.\n\n"
    "Отвечай коротко, живо, по делу. Используй неформальный тон, лёгкую иронию и подколки, "
    "но без грубости и оскорблений."
)


app = Flask(__name__)

client = Groq(
    api_key=GROQ_API_KEY
)


VK_API_URL = "https://api.vk.com/method/messages.send"
VK_API_VERSION = "5.199"


# =========================================================
# МОДЕЛИ
# =========================================================

MAIN_MODEL = "openai/gpt-oss-120b"
BACKUP_MODEL = "openai/gpt-oss-20b"


# =========================================================
# АВТОМАТИЧЕСКОЕ ПЕРЕКЛЮЧЕНИЕ
# =========================================================

# После ошибки 429 ждём 1 час перед новой проверкой 120B
MAIN_MODEL_RETRY_TIME = 60 * 60

# До какого времени 120B считается временно недоступной
main_model_blocked_until = 0


def is_rate_limit_error(error):
    """
    Проверяем, является ли ошибка ошибкой лимита Groq.
    """

    error_text = str(error).lower()

    return (
        "429" in error_text
        or "rate limit" in error_text
        or "rate_limit_exceeded" in error_text
        or "tokens per day" in error_text
        or "tpd" in error_text
    )


def ask_model(model: str, user_message: str) -> str:
    """
    Отправляет запрос выбранной модели Groq.
    """

    completion = client.chat.completions.create(
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


def ask_groq(user_message: str) -> str:
    """
    Логика:

    120B → если лимит → 20B

    Через час после 429 снова пробуем 120B.
    """

    global main_model_blocked_until

    current_time = time.time()


    # =====================================================
    # ПРОВЕРЯЕМ, МОЖНО ЛИ СНОВА ПОПРОБОВАТЬ 120B
    # =====================================================

    if current_time >= main_model_blocked_until:

        try:

            print(
                "Пробуем основную модель:",
                MAIN_MODEL,
                flush=True
            )

            reply = ask_model(
                MAIN_MODEL,
                user_message
            )

            # 120B снова работает
            main_model_blocked_until = 0

            print(
                "120B снова доступна.",
                flush=True
            )

            return reply


        except Exception as e:

            if is_rate_limit_error(e):

                # Блокируем 120B на 1 час
                main_model_blocked_until = (
                    time.time()
                    + MAIN_MODEL_RETRY_TIME
                )

                print(
                    "Лимит 120B достигнут.",
                    flush=True
                )

                print(
                    "Переходим на 20B.",
                    flush=True
                )

                print(
                    "Повторная проверка 120B через 1 час.",
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


    # =====================================================
    # ЗАПАСНАЯ МОДЕЛЬ 20B
    # =====================================================

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


def send_vk_message(peer_id: int, text: str):

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


@app.route("/callback", methods=["POST"])
def callback():

    data = request.get_json(force=True)


    # =====================================================
    # ПРОВЕРКА СЕКРЕТА
    # =====================================================

    if (
        VK_GROUP_SECRET
        and data.get("secret") != VK_GROUP_SECRET
    ):
        return "invalid secret", 403


    event_type = data.get("type")


    # =====================================================
    # ПОДТВЕРЖДЕНИЕ CALLBACK API
    # =====================================================

    if event_type == "confirmation":

        return VK_CONFIRMATION_CODE


    # =====================================================
    # НОВОЕ СООБЩЕНИЕ
    # =====================================================

    if event_type == "message_new":

        message = data["object"]["message"]

        user_id = message["from_id"]
        peer_id = message["peer_id"]
        text = message.get("text", "")


        if text.strip():

            try:

                reply = ask_groq(text)


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


            # Отправляем ответ туда,
            # откуда пришло сообщение
            send_vk_message(
                peer_id,
                reply
            )


        return "ok"


    return "ok"


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
