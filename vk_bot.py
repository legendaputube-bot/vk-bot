import os
import re
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
    "Ты — ИИ-бот сообщества 'Бонус-коды Tanks blitz' ВКонтакте, посвящённого ИСКЛЮЧИТЕЛЬНО "
    "игре Tanks Blitz PVP битвы (разработчик EAST-GAMES LLC / Lesta Games). Ты — часть "
    "админской команды сообщества, свой парень среди танкистов. Ты не просто справочник, "
    "а участник тусовки: подкалываешь игроков по-дружески, угараешь вместе с ними, "
    "поддерживаешь живой разговор, помнишь, о чём говорили с человеком раньше (тебе для "
    "этого дают историю последних сообщений).\n\n"

    "Если разговор уходит совсем далеко от игры — дерзко и с юмором подкалывай и мягко "
    "возвращай к танкам, но не будь занудой — лёгкий стёб на отвлечённые темы допустим, "
    "если это часть живого общения с человеком, просто не отвечай по существу на "
    "посторонние вопросы (советы, факты, помощь не по теме).\n\n"

    "ПАМЯТЬ: используй историю переписки с этим человеком, чтобы вести связный диалог, "
    "шутить над тем, что он говорил раньше, помнить контекст.\n\n"

    "ОБРАЩЕНИЕ ПО ИМЕНИ: тебе передаётся имя пользователя в формате '[Имя: ...]' в начале "
    "сообщения. Обращайся по имени естественно. Саму пометку в ответе не показывай.\n\n"

    "ЗАПРЕТ НА ВЫДУМЫВАНИЕ ТОЧНЫХ ЦИФР: не придумывай точные характеристики техники, "
    "калибры, урон, броню, валюту — ты их не знаешь. За конкретикой отправляй смотреть "
    "гайды на YouTube.\n\n"

    "ФОРМАТ ОТВЕТА: КОРОТКО, максимум 2-3 предложения. Никаких портянок текста и "
    "никаких технических пометок/тегов — только чистый финальный ответ.\n\n"

    "Тон: неформальный, дерзкий, с иронией и подколками, но без грубости и оскорблений "
    "в адрес самого человека. Дерзость — смешная, а не обидная."
)


app = Flask(__name__)
client = Groq(api_key=GROQ_API_KEY)

VK_API_URL = "https://api.vk.com/method/messages.send"
VK_USERS_GET_URL = "https://api.vk.com/method/users.get"
VK_API_VERSION = "5.199"

MAIN_MODEL = "openai/gpt-oss-120b"
BACKUP_MODEL = "openai/gpt-oss-20b"
VISION_MODEL = "qwen/qwen3.6-27b"
MAIN_MODEL_RETRY_TIME = 60 * 60  # 1 час
main_model_blocked_until = 0

MAX_HISTORY_MESSAGES = 5  # сколько последних пар сообщений помнить на человека
conversation_history = {}
history_lock = threading.Lock()


def clean_response(text: str) -> str:
    """Убирает технический блок размышлений <think>...</think> из ответа."""
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    return cleaned.strip()


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


def get_history(user_id: int):
    with history_lock:
        return list(conversation_history.get(user_id, []))


def add_to_history(user_id: int, user_message: str, bot_reply: str):
    with history_lock:
        history = conversation_history.get(user_id, [])
        history.append({"role": "user", "content": user_message})
        history.append({"role": "assistant", "content": bot_reply})
        # оставляем только последние MAX_HISTORY_MESSAGES пар (юзер+бот)
        max_items = MAX_HISTORY_MESSAGES * 2
        conversation_history[user_id] = history[-max_items:]


def ask_model(model, user_message, user_name, user_id):
    message_with_name = f"[Имя: {user_name}] {user_message}" if user_name else user_message

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend(get_history(user_id))
    messages.append({"role": "user", "content": message_with_name})

    completion = client.chat.completions.create(
        model=model,
        messages=messages,
        max_tokens=300,
    )
    reply = clean_response(completion.choices[0].message.content)
    add_to_history(user_id, user_message, reply)
    return reply


def ask_groq(user_message: str, user_name: str, user_id: int) -> str:
    global main_model_blocked_until

    current_time = time.time()

    if current_time >= main_model_blocked_until:
        try:
            print("Пробуем основную модель:", MAIN_MODEL, flush=True)
            reply = ask_model(MAIN_MODEL, user_message, user_name, user_id)
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
    return ask_model(BACKUP_MODEL, user_message, user_name, user_id)


def transcribe_voice(audio_url: str) -> str:
    audio_response = requests.get(audio_url, timeout=15)
    audio_response.raise_for_status()

    transcription = client.audio.transcriptions.create(
        file=("voice.ogg", audio_response.content),
        model="whisper-large-v3",
        language="ru",
    )
    return transcription.text


def ask_about_image(image_url: str, user_name: str, user_id: int, caption: str = "") -> str:
    prompt_text = caption.strip() if caption.strip() else "Что на этом скриншоте? Прокомментируй в своём стиле."
    message_with_name = f"[Имя: {user_name}] {prompt_text}" if user_name else prompt_text

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend(get_history(user_id))
    messages.append({
        "role": "user",
        "content": [
            {"type": "text", "text": message_with_name},
            {"type": "image_url", "image_url": {"url": image_url}},
        ],
    })

    completion = client.chat.completions.create(
        model=VISION_MODEL,
        messages=messages,
        max_tokens=300,
    )
    reply = clean_response(completion.choices[0].message.content)
    add_to_history(user_id, prompt_text, reply)
    return reply


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
        reply = ask_groq(text, user_name, from_id)
    except Exception as e:
        reply = "Что-то я сейчас подвис 😅 Попробуй написать ещё раз."
        print("Ошибка при обращении к Groq:", e, flush=True)
    send_vk_message(peer_id, reply)


def handle_voice_message(peer_id: int, from_id: int, voice_url: str):
    try:
        user_name = get_user_name(from_id)
        text = transcribe_voice(voice_url)
        print("Распознан голос:", text, flush=True)
        reply = ask_groq(text, user_name, from_id)
    except Exception as e:
        reply = "Не смог разобрать голосовое 😅 Попробуй написать текстом."
        print("Ошибка при распознавании голоса:", e, flush=True)
    send_vk_message(peer_id, reply)


def handle_image_message(peer_id: int, from_id: int, image_url: str, caption: str):
    try:
        user_name = get_user_name(from_id)
        reply = ask_about_image(image_url, user_name, from_id, caption)
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
