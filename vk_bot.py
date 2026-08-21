import os
import requests
from flask import Flask, request, jsonify

# ==== НАСТРОЙКИ ====
VK_TOKEN = os.environ.get("VK_TOKEN", "vk1.a.KG4nW1LR4LHDNR_J_u_U1iGAoj9Aa48n6KxrVF95bibnoatshXV57finEWLCxCvIUYU2HYzoFiJzUIVcJD7MdS-WnBSYAFNYcdZrV1xPh-InFXJ2WednojQ2qqYNHa-EN4TGkBGaMJ8OivB0CB4L2wQAHLCn2DLGB8ei6DFpoVZ0bbhCfBxqLyGpCOfvFHOhn9glR8RDbS68_3-pHPPddQ")
VK_CONFIRMATION_CODE = os.environ.get("VK_CONFIRMATION_CODE", "ec409dba")
VK_GROUP_SECRET = os.environ.get("VK_GROUP_SECRET", "aaQ13axAPQEcczQa")

app = Flask(__name__)

VK_API_URL = "https://vk.com"
VK_API_VERSION = "5.199"

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
        
        # Временно просто зеркалим текст пользователя, пока нет ключа ИИ
        text = message.get("text", "")
        if text.strip():
            send_vk_message(user_id, f"Вы написали: {text}\n(Бот работает, скоро подключим ИИ!)")

        return "ok"

    return "ok"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
