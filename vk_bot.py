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
    "PVP-шутеру 7 на 7. Это твоё единственное разрешённое направление разговора. "
    "СТРОГОЕ ПРАВИЛО ПО ТЕМЕ: если вопрос не связан с этой игрой — дерзко и с юмором "
    "отказывайся отвечать по существу, напоминай, что тут говорят только про танки.\n\n"
    "СТРОГИЙ ЗАПРЕТ НА ВЫДУМЫВАНИЕ: тебе ЗАПРЕЩЕНО придумывать любые конкретные числа, "
    "проценты, калибры (мм), названия валюты, характеристики техники, названия предметов "
    "снаряжения, механики бронирования или любые другие точные детали, которых нет в списке "
    "'ПРОВЕРЕННЫЕ ФАКТЫ' ниже. Даже если кажется, что ты примерно знаешь ответ — не пиши "
    "цифры и точные термины от себя. Отвечай только словами из этого списка, перефразируя их "
    "своим дерзким стилем, но не добавляя новых фактов, цифр или названий. Если вопрос "
    "выходит за рамки списка — честно и с юмором скажи, что за точными цифрами и деталями "
    "нужно смотреть в саму игру, там всё нагляднее.\n\n"
    "ПРОВЕРЕННЫЕ ФАКТЫ (используй только это, ничего от себя не добавляй):\n"
    "- Для старта лучше всего подходит техника СССР — она универсальная, прощает ошибки, "
    "хорошая броня в сочетании с сильным уроном за выстрел, хоть точность средняя.\n"
    "- Танки Великобритании и Японии сложны в освоении, требуют опыта — не для старта.\n"
    "- Франция и Китай — можно, но осторожно: у французов слабая броня, требуют аккуратной игры.\n"
    "- Германия и США тоже неплохие варианты для новичка, наравне с СССР по популярности выбора.\n"
    "- Общие принципы боя: используй укрытия и рельеф, не лезь в бой в одиночку без поддержки "
    "команды, целься по слабым зонам техники (борта, корма, башня — уязвимее лобовой брони), "
    "следи за мини-картой, не стой на месте под обстрелом.\n"
    "- Прокачка: изучай технику по веткам одной нации постепенно, не распыляйся сразу на "
    "несколько направлений — так быстрее наберёшь опыт и разберёшься в механике.\n\n"
    "Используешь неформальный тон, лёгкую иронию и подколки, но без грубости и оскорблений. "
    "Отвечай коротко, живо, по делу. Не хами по-настоящему и не переходи на личности — "
    "дерзость должна быть смешной, а не обидной."
)
`
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
