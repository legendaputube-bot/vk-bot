import os
import requests
import time
import threading
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
    "'[Имя: ...]'. Обращайся к человеку по этому имени в своём ответе, естественно вписывая "
    "его в дерзкий стиль. Саму пометку '[Имя: ...]' в ответе не показывай.\n\n"

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


app = Flask(__name__)
client = Groq(api_key=GROQ_API_KEY)

VK_API_URL = "https://api.vk.com/method/messages.send"
VK_USERS_GET_URL = "https://api.vk.com/method/users.get"
VK_API_VERSION = "5.199"

MAIN_MODEL = "openai/gpt-oss-120b"
BACKUP_MODEL = "openai/gpt-oss-20b"
VISION_MODEL = "meta-llama/llama-4-scout-17b-16e-instruct"
MAIN_MODEL_RETRY_TIME = 60 * 60  # 1 час
main_model_blocked_until = 0


def is_rate_limit_error(error):
    error_text = str(error).lower()
    return (
        "429" in error_text
        or "rate limit" in error_text
        or "rate_limit_exceeded" in error_text
        or "tokens per day" in error_text
        or "tpd" in error_text
    )


def get_user_name(user_id: int) -> str:
    try:
        params = {
            "access_token": VK_TOKEN,
            "v": VK_API_VERSION,
            "user_ids": user_id,
        }
        response = requests.get(VK_USERS_GET_URL, params=params, timeout=10)
        result = response.json()
        first_name = result["response"][0]["first_name"]
        return first_name
    except Exception as e:
        print("Не удалось получить имя пользователя:", e, flush=True)
        return ""


def ask_model(model, user_message, user_name):
    message_with_name = f"[Имя: {user_name}] {user_message}" if user_name else user_message

    completion = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": message_with_name},
        ],
        max_tokens=300,
    )
    return completion.choices[0].message.content


def ask_groq(user_message: str, user_name: str) -> str:
    global main_model_blocked_until

    current_time = time.time()

    if current_time >= main_model_blocked_until:
        try:
            print("Пробуем основную модель:", MAIN_MODEL, flush=True)
            reply = ask_model(MAIN_MODEL, user_message, user_name)
            main_model_blocked_until = 0
            print("120B работает. Используем основную модель.", flush=True)
            return reply
        except Exception as e:
            if is_rate_limit_error(e):
                main_model_blocked_until = time.time() + MAIN_MODEL_RETRY_TIME
                print("Лимит 120B достигнут. Переходим на 20B.", flush=True)
            else:
                print("Ошибка 120B:", e, flush=True)
                print("Временно используем 20B.", flush=True)

    print("Используем запасную модель:", BACKUP_MODEL, flush=True)
    return ask_model(BACKUP_MODEL, user_message, user_name)


def transcribe_voice(audio_url: str) -> str:
    audio_response = requests.get(audio_url, timeout=15)
    audio_response.raise_for_status()

    transcription = client.audio.transcriptions.create(
        file=("voice.ogg", audio_response.content),
        model="whisper-large-v3",
        language="ru",
    )
    return transcription.text


def ask_about_image(image_url: str, user_name: str, caption: str = "") -> str:
    prompt_text = caption.strip() if caption.strip() else "Что на этом скриншоте? Прокомментируй в своём стиле."
    message_with_name = f"[Имя: {user_name}] {prompt_text}" if user_name else prompt_text

    completion = client.chat.completions.create(
        model=VISION_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": message_with_name},
                    {"type": "image_url", "image_url": {"url": image_url}},
                ],
            },
        ],
        max_tokens=300,
    )
    return completion.choices[0].message.content


def send_vk_message(peer_id: int, text: str):
    params = {
        "access_token": VK_TOKEN,
        "v": VK_API_VERSION,
        "peer_id": peer_id,
        "message": text,
        "random_id": 0,
    }
    response = requests.post(VK_API_URL, data=params, timeout=15)
    result = response.json()
    if "error" in result:
        print("Ошибка VK API:", result["error"], flush=True)
    return result


def handle_message(peer_id: int, from_id: int, text: str):
    try:
        user_name = get_user_name(from_id)
        reply = ask_groq(text, user_name)
    except Exception as e:
        reply = "Что-то я сейчас подвис 😅 Попробуй написать ещё раз."
        print("Ошибка при обращении к Groq:", e, flush=True)
    send_vk_message(peer_id, reply)


def handle_voice_message(peer_id: int, from_id: int, voice_url: str):
    try:
        user_name = get_user_name(from_id)
        text = transcribe_voice(voice_url)
        print("Распознан голос:", text, flush=True)
        reply = ask_groq(text, user_name)
    except Exception as e:
        reply = "Не смог разобрать голосовое 😅 Попробуй написать текстом."
        print("Ошибка при распознавании голоса:", e, flush=True)
    send_vk_message(peer_id, reply)


def handle_image_message(peer_id: int, from_id: int, image_url: str, caption: str):
    try:
        user_name = get_user_name(from_id)
        reply = ask_about_image(image_url, user_name, caption)
    except Exception as e:
        reply = "Не смог рассмотреть скриншот 😅 Попробуй ещё раз или опиши словами."
        print("Ошибка при анализе изображения:", e, flush=True)
    send_vk_message(peer_id, reply)


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
        peer_id = message["peer_id"]
        from_id = message["from_id"]
        text = message.get("text", "")
        attachments = message.get("attachments", [])

        voice_url = None
        image_url = None

        for att in attachments:
            if att.get("type") == "audio_message":
                audio_message = att.get("audio_message", {})
                voice_url = audio_message.get("link_ogg") or audio_message.get("link_mp3")
            elif att.get("type") == "photo":
                photo = att.get("photo", {})
                sizes = photo.get("sizes", [])
                if sizes:
                    image_url = sizes[-1]["url"]

        if voice_url:
            threading.Thread(
                target=handle_voice_message,
                args=(peer_id, from_id, voice_url)
            ).start()
        elif image_url:
            threading.Thread(
                target=handle_image_message,
                args=(peer_id, from_id, image_url, text)
            ).start()
        elif text.strip():
            threading.Thread(
                target=handle_message,
                args=(peer_id, from_id, text)
            ).start()

        return "ok"

    return "ok"


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
