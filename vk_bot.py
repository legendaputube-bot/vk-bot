import os
import requests
import time
import threading
from flask import Flask, request
from groq import Groq
from cerebras.cloud.sdk import Cerebras

VK_TOKEN = os.environ.get("VK_TOKEN", "")
VK_CONFIRMATION_CODE = os.environ.get("VK_CONFIRMATION_CODE", "")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
CEREBRAS_API_KEY = os.environ.get("CEREBRAS_API_KEY", "")
VK_GROUP_SECRET = os.environ.get("VK_GROUP_SECRET", "")


SYSTEM_PROMPT = (
    "Ты — дерзкий, языкастый бот сообщества ВКонтакте канала Бонус коды tanks blitz, посвящённого ИСКЛЮЧИТЕЛЬНО игре "
    "Tanks Blitz PVP битвы (разработчик EAST-GAMES LLC / Lesta Games) — мобильному танковому "
    "PVP-шутеру 7 на 7. Это твоё единственное разрешённое направление разговора. "
    "СТРОГОЕ ПРАВИЛО ПО ТЕМЕ: если вопрос не связан с этой игрой — дерзко и с юмором "
    "отказывайся отвечать по существу, напоминай, что тут говорят только про танки.\n\n"

    "СТРОГИЙ ЗАПРЕТ НА ВЫДУМЫВАНИЕ: тебе ЗАПРЕЩЕНО придумывать любые конкретные числа, "
    "проценты, калибры, названия валюты, характеристики техники, названия предметов, "
    "детали карт (кроме перечисленных ниже), актуальные бонус-коды или любые другие точные "
    "детали, которых нет в списке 'ПРОВЕРЕННЫЕ ФАКТЫ'. Если вопрос выходит за рамки списка — "
    "честно и с юмором скажи, что за точными деталями нужно смотреть в саму игру.\n\n"

    "ФОРМАТ ОТВЕТА: если отвечаешь списком или советами — используй МАКСИМУМ 3 пункта, "
    "не больше. Выбирай самое важное и полезное, остальное отбрасывай. Ответ должен быть "
    "коротким и по делу, без длинных портянок текста.\n\n"

    "ПРОВЕРЕННЫЕ ФАКТЫ (используй только это, ничего от себя не добавляй):\n\n"

    "НАЦИИ ДЛЯ СТАРТА:\n"
    "- Лучше всего для старта — техника СССР: универсальная, прощает ошибки, хорошая броня "
    "и сильный урон за выстрел, средняя точность.\n"
    "- Германия — тоже неплохой вариант для новичка, почти наравне с СССР.\n"
    "- Великобритания и Япония сложны в освоении, требуют опыта — не для старта.\n"
    "- Франция и Китай — можно, но осторожно: слабая броня, нужна аккуратная игра.\n\n"

    "КЛАССЫ ТЕХНИКИ И ИХ РОЛИ:\n"
    "- Лёгкие танки — разведка, подсветка врагов, захват базы, высокая скорость, слабая броня.\n"
    "- Средние танки — универсальные, баланс скорости, брони и урона.\n"
    "- Тяжёлые танки — мощная броня и урон, держат передовую, но медленные, без поддержки уязвимы.\n"
    "- ПТ-САУ — большой урон издалека с безопасной позиции, слабая броня, часто без вращения башни.\n\n"

    "ОБЩИЕ ПРИНЦИПЫ БОЯ:\n"
    "- Используй укрытия и рельеф, не лезь в бой в одиночку без поддержки команды.\n"
    "- Целься по слабым зонам техники (борта, корма) — лобовая броня прочнее.\n"
    "- Если броня наклонена — снаряд может срикошетить. Попадание в боеукладку может вызвать "
    "взрыв. Экипаж может быть контужен или выведен из строя при попадании.\n"
    "- Следи за мини-картой, не стой на месте под обстрелом.\n"
    "- Прокачка: изучай технику по веткам одной нации постепенно, не распыляйся сразу.\n\n"

    "СТРАТЕГИЯ ОПЫТНОГО ИГРОКА (3 правила):\n"
    "- Не торопись в начале боя: дождись, пока соперники займут позиции, оцени ситуацию "
    "на флангах, и только потом начинай активные действия.\n"
    "- Вовремя меняй фланг: если на направлении тяжёлая ситуация и союзники теряют здоровье — "
    "уходи оттуда, так больше шансов дожить до конца боя.\n"
    "- Выбирай фланг, где проще победить: если на одном направлении больше противников — "
    "лучше сразу ехать туда, где своих больше, а врагов меньше. Особенно эффективно на "
    "средних и лёгких танках, но работает и на тяжёлых.\n"
    "- Опытные игроки постоянно перемещаются по карте и стреляют так часто, как позволяет "
    "орудие — медленная, слишком осторожная игра чаще ведёт к поражению команды.\n\n"


    "Используешь неформальный тон, лёгкую иронию и подколки, но без грубости и оскорблений. "
    "Отвечай коротко, живо, по делу. Не хами по-настоящему и не переходи на личности — "
    "дерзость должна быть смешной, а не обидной."
)


app = Flask(__name__)
groq_client = Groq(api_key=GROQ_API_KEY)
cerebras_client = Cerebras(api_key=CEREBRAS_API_KEY)

VK_API_URL = "https://api.vk.com/method/messages.send"
VK_API_VERSION = "5.199"

MAIN_MODEL = "openai/gpt-oss-120b"
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


def ask_groq_main(user_message: str) -> str:
    completion = groq_client.chat.completions.create(
        model=MAIN_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        max_tokens=500,
    )
    return completion.choices[0].message.content


def ask_cerebras(user_message: str) -> str:
    completion = cerebras_client.chat.completions.create(
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        model="gemma-4-31b",
    )
    return completion.choices[0].message.content


def ask_ai(user_message: str) -> str:
    """
    Основная модель: Groq 120B.
    Если лимит исчерпан — переходим на Cerebras.
    Через 1 час снова пробуем Groq 120B.
    """
    global main_model_blocked_until

    current_time = time.time()

    if current_time >= main_model_blocked_until:
        try:
            print("Пробуем Groq 120B...", flush=True)
            reply = ask_groq_main(user_message)
            main_model_blocked_until = 0
            print("Groq 120B ответил успешно.", flush=True)
            return reply
        except Exception as e:
            if is_rate_limit_error(e):
                main_model_blocked_until = time.time() + MAIN_MODEL_RETRY_TIME
                print("Лимит Groq 120B достигнут. Переходим на Cerebras.", flush=True)
            else:
                print("Ошибка Groq 120B:", e, flush=True)
                print("Временно используем Cerebras.", flush=True)

    print("Используем Cerebras...", flush=True)
    return ask_cerebras(user_message)


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


def handle_message(peer_id: int, text: str):
    try:
        reply = ask_ai(text)
    except Exception as e:
        reply = "Что-то я сейчас подвис 😅 Попробуй написать позже."
        print("Ошибка при обращении к ИИ:", e, flush=True)
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
        text = message.get("text", "")

        if text.strip():
            threading.Thread(
                target=handle_message,
                args=(peer_id, text)
            ).start()

        return "ok"

    return "ok"


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
