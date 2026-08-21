import os
import requests
from flask import Flask, request, jsonify
from anthropic import Anthropic

# ==== НАСТРОЙКИ ====
VK_TOKEN = os.environ.get("VK_TOKEN", "ВАШ_ТОКЕН_СООБЩЕСТВА_ВК")
VK_CONFIRMATION_CODE = os.environ.get("VK_CONFIRMATION_CODE", "СТРОКА_ПОДТВЕРЖДЕНИЯ_ИЗ_ВК")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "ВАШ_КЛЮЧ_ANTHROPIC")
VK_GROUP_SECRET = os.environ.get("VK_GROUP_SECRET", "")

SYSTEM_PROMPT = (
    "Ты — дружелюбный помощник сообщества ВКонтакте. "
    "Отвечай кратко, вежливо и по делу на сообщения пользователей."
)

app = Flask(__name__)
client = Anthropic(api_key=ANTHROPIC_API_KEY)

VK_API_URL = "https://api.vk.com/method/messages.send"
VK_API_VERSION = "5.199"


def ask_claude(user_message: str) -> str:
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=500,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )
    return "".join(block.text for block in response.content if block.type == "text")


def send_vk_message(user_id: int, text: str):
    params = {
        "access_token": VK_TOKEN,
        "v": VK_API_VERSION,
        "user_id": user_id,
        "message": text,
        "random_id": 0,
    }
    r = requests.post(VK_API_URL, data=params, timeout=15)
    return r.json()


@app.route("/callback", methods=["POST"])
def callback():
    data = request.get_json(force=True)

    if VK_GROUP_SECRET and data.get("secret") != VK_GROUP_SECRET:
        return "invalid secret", 403

    event_type = data.get("type")

    if event_type == "confirmation":
        return VK_CONFIRMATION_CODE

    if event_type == "message_new":
        message = data["object"]["message"]
        user_id = message["from_id"]
        text = message.get("text", "")

        if text.strip():
            try:
                reply = ask_claude(text)
            except Exception as e:
                reply = "Извините, произошла ошибка. Попробуйте позже."
                print("Ошибка при обращении к Claude:", e)

            send_vk_message(user_id, reply)

        return "ok"

    return "ok"


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
