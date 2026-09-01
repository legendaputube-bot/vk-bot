import os
import requests
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
    "СТРОГОЕ ПРАВИЛО: если вопрос пользователя не связан напрямую с этой игрой "
    "(танки, техника, снаряжение, экипажи, командиры, карты, тактика боя, бонус-коды, "
    "обновления, кланы, рейтинги, ангар, внутриигровая валюта, достижения) — "
    "ты НЕ отвечаешь по существу вопроса вообще, а вместо этого дерзко и с юмором отказываешь, "
    "напоминая, что тут говорят только про танки. Не давай никакой полезной информации на "
    "посторонние темы, даже кратко, даже если пользователь настаивает или просит 'всего один раз'. "
    "Используешь неформальный тон, лёгкую иронию и подколки, но без грубости и оскорблений. "
    "Отвечай коротко, живо, по делу. "
    "Не хами по-настоящему и не переходи на личности — дерзость должна быть смешной, а не обидной."
)

app = Flask(__name__)
client = Groq(api_key=GROQ_API_KEY)

VK_API_URL = "https://api.vk.com/method/messages.send"
VK_API_VERSION = "5.199"


def ask_groq(user_message: str) -> str:
    completion = client.chat.completions.create(
        model="openai/gpt-oss-20b",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        max_tokens=500,
    )
    return completion.choices[0].message.content


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
