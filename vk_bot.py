import os
import requests
from flask import Flask, request

# ==== НАСТРОЙКИ ====
VK_TOKEN = os.environ.get("VK_TOKEN", "")
VK_CONFIRMATION_CODE = os.environ.get("VK_CONFIRMATION_CODE", "")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
VK_GROUP_SECRET = os.environ.get("VK_GROUP_SECRET", "")

SYSTEM_PROMPT = (
    "Ты — дерзкий, языкастый помощник сообщества ВКонтакте, отвечаешь в стиле молодёжного сленга. "
    "Используешь неформальный тон, лёгкую иронию и подколки, но без грубости и оскорблений. "
    "Отвечай коротко, живо, по делу — без канцелярита и занудства. "
    "Не хами пользователям по-настоящему и не переходи на личности — дерзость должна быть смешной, а не обидной."
)

app = Flask(__name__)

VK_API_URL = "https://api.vk.com/method/messages.send"
VK_API_VERSION = "5.199"
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"


def ask_groq(user_message: str) -> str:
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "llama-3.1-8b-instant",
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        "max_tokens": 500,
    }
    r = requests.post(GROQ_API_URL, headers=headers, json=payload, timeout=30)
    r.raise_for_status()
    data = r.json()
    return data["choices"][0]["message"]["content"]


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
                reply = ask_groq(text)
            except Exception as e:
                reply = "Извините, произошла ошибка. Попробуйте позже."
                print("Ошибка при обращении к Groq:", e, flush=True)

            send_vk_message(user_id, reply)

        return "ok"

    return "ok"


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
