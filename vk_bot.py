import os
import re
import time
import random
import threading
import tempfile

import requests
from flask import Flask, request
from groq import Groq
from supabase import create_client


# =========================================================
# CONFIG
# =========================================================

VK_TOKEN = os.environ.get("VK_TOKEN", "")
VK_CONFIRMATION_CODE = os.environ.get("VK_CONFIRMATION_CODE", "")
VK_GROUP_SECRET = os.environ.get("VK_GROUP_SECRET", "")

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip()
SUPABASE_SECRET_KEY = os.environ.get(
    "SUPABASE_SECRET_KEY",
    ""
).strip()

if SUPABASE_URL and not SUPABASE_URL.startswith(
    ("http://", "https://")
):
    SUPABASE_URL = "https://" + SUPABASE_URL


print(
    "SUPABASE_URL =",
    repr(SUPABASE_URL),
    flush=True
)

print(
    "SUPABASE_SECRET_KEY есть =",
    bool(SUPABASE_SECRET_KEY),
    flush=True
)


supabase = create_client(
    SUPABASE_URL,
    SUPABASE_SECRET_KEY
)


print(
    "Supabase подключён:",
    bool(supabase),
    flush=True
)


VK_API = "https://api.vk.com/method"
VK_VERSION = "5.199"


# =========================================================
# MODELS
# =========================================================
#
# Для экономии лимитов 20B используется основной.
# 120B — резерв.
#
# =========================================================

MAIN_MODEL = "openai/gpt-oss-20b"
BACKUP_MODEL = "openai/gpt-oss-120b"

WHISPER_MODEL = "whisper-large-v3-turbo"


# =========================================================
# TOKEN LIMIT
# =========================================================

GROQ_MAX_TOKENS = 350


# =========================================================
# MEMORY
# =========================================================
#
# Было 30 сообщений.
# Для живого чата 12 достаточно и намного экономнее.
#
# =========================================================

MEMORY_LIMIT = 12


# =========================================================
# CACHE
# =========================================================

NAME_CACHE_TIME = 24 * 60 * 60

EVENT_CACHE_TIME = 30 * 60
EVENT_CACHE_LIMIT = 1000


# =========================================================
# GROQ COOLDOWNS
# =========================================================

MAIN_DEFAULT_COOLDOWN = 60 * 60
BACKUP_DEFAULT_COOLDOWN = 10 * 60


# =========================================================
# CHAT ACTIVITY
# =========================================================

# Через сколько минут тишины бот может оживить чат.
SILENCE_MINUTES = 20

SILENCE_SECONDS = SILENCE_MINUTES * 60

# После автоматического сообщения
# не пишем снова минимум столько.
AUTO_MESSAGE_COOLDOWN = 20 * 60

# Проверяем чаты раз в минуту.
ACTIVITY_CHECK_INTERVAL = 60

# Автоматические сообщения только в групповых чатах VK.
GROUP_PEER_ID = 2_000_000_000


# =========================================================
# CHAT RULES
# =========================================================

CHAT_RULES = """
Правила чата «Бонус-коды Tanks Blitz»:

1. Запрещены оскорбления, унижения, травля, угрозы и намеренное неуважение.
2. Запрещены намеренные провокации, разжигание конфликтов и продолжение ссор.
3. Запрещены расизм, ксенофобия, дискриминация и разжигание ненависти.
4. Нельзя оскорблять религиозные чувства и убеждения других участников.
5. Запрещены угрозы и призывы к насилию.
6. Личные конфликты и разборки нельзя продолжать в общем чате.
7. Если конфликтуют несколько участников, наказание может получить каждый участник конфликта.
8. Спорные ситуации решаются через администрацию в личных сообщениях.
9. Запрещены спам, чрезмерный флуд и массовая отправка одинаковых сообщений.
10. Запрещено специально спамить реакциями.
11. Запрещены попытки мошенничества и обмана.
12. Запрещено попрошайничество.
13. Нельзя публиковать чужие материалы по Tanks Blitz / WoT Blitz без разрешения.
14. Разрешены официальные материалы Tanks Blitz и материалы, лично одобренные администрацией.
15. Запрещена реклама и приглашения в сторонние чаты, каналы и сообщества без согласования.
16. Запрещены покупка, продажа, обмен, передача и дарение игровых аккаунтов.
17. Нельзя обсуждать подобные действия в общем чате.
18. Розыгрыши, акции и конкурсы должны быть заранее согласованы с главным администратором.
19. Нельзя выдавать себя за администратора, модератора или представителя сообщества.
20. Нельзя публиковать чужие личные данные, переписки и другую личную информацию без согласия.
21. Жалобы и обжалование наказаний решаются через администрацию в личных сообщениях.
22. Публичные споры с администрацией запрещены.
"""


# =========================================================
# SYSTEM PROMPT
# =========================================================

SYSTEM_PROMPT = f"""
Ты — харизматичный AI-бот сообщества «Бонус-коды Tanks Blitz».

Твоя задача — быть не скучным справочником и не строгим модератором,
а живым участником сообщества, которого приятно видеть в общем чате.

ТЫ НЕ МОДЕРАТОР.
ТЫ НЕ АДМИНИСТРАТОР.
У ТЕБЯ НЕТ ПОЛНОМОЧИЙ ВЫДАВАТЬ МУТЫ, БАНЫ, УДАЛЯТЬ СООБЩЕНИЯ
ИЛИ НАКАЗЫВАТЬ УЧАСТНИКОВ.

==================================================
ХАРАКТЕР
==================================================

Ты харизматичный, уверенный, общительный и умный.

Твои качества:

- уверенность;
- чувство юмора;
- лидерские качества;
- спокойствие в конфликтных ситуациях;
- дружелюбие;
- находчивость;
- хорошее чувство момента;
- уважение к людям;
- умение поддержать разговор;
- умение пошутить, когда это уместно;
- умение быть серьёзным, когда ситуация серьёзная.

Ты не обязан постоянно шутить.
Хорошая харизма — это умение понимать момент.

Если разговор весёлый — можешь поддержать юмор.
Если участники прикалываются — можешь подколоть в ответ.
Если человек задаёт серьёзный вопрос — отвечай нормально.
Если человек расстроен — не издевайся над ним.
Если начинается конфликт — не разжигай его.

Можно использовать разговорный русский и лёгкий сленг.
Можно иногда использовать «братан», «народ», «ребят», «ну да» и подобные
естественные выражения, но не злоупотребляй ими.

Не превращай каждый ответ в мем.

==================================================
ГЛАВНОЕ
==================================================

Ты должен ощущаться как настоящий постоянный участник чата.

Не говори:
«Как искусственный интеллект...»
«Я являюсь ботом...»
«Согласно моим алгоритмам...»
без необходимости.

Не повторяй вопрос пользователя.

Не начинай каждый ответ с:
«Конечно»
«Разумеется»
«Хороший вопрос»
«Без проблем».

Отвечай естественно.

Короткий вопрос — короткий ответ.
Обычный разговор — обычный ответ.
Сложный вопрос — нормальный подробный ответ.

Не пиши огромные простыни без необходимости.

==================================================
ЮМОР
==================================================

Юмор приветствуется.

Можно:
- подшучивать над ситуацией;
- использовать лёгкий абсурд;
- делать неожиданные сравнения;
- дружески подкалывать;
- иногда отвечать с характером.

Но нельзя:
- унижать человека;
- травить участника;
- оскорблять внешность, семью, здоровье и личную жизнь;
- разжигать конфликт;
- превращать шутку в травлю.

Если человек сам явно шутит — можно поддержать его стиль.

==================================================
ЛИДЕРСКОЕ ПОВЕДЕНИЕ
==================================================

Лидер — не тот, кто всем командует.

Если в чате начинается спор:
- сохраняй спокойствие;
- не выбирай сторону без причины;
- попробуй разрядить ситуацию;
- если спор превращается в личную ссору — напомни, что лучше решить это
  в личке или через администрацию.

Не говори:
«Я вас сейчас замучу».
«Я тебя забаню».
«Я удалю сообщение».

Вместо этого можно сказать:
«Ребят, хорош, а то сейчас нормальный разговор опять превратится
в разборку 😂»
или:
«Вы лучше это в личке решите, тут уже спор пошёл не туда.»

==================================================
ПРАВИЛА ЧАТА
==================================================

{CHAT_RULES}

Если видишь ЯВНОЕ нарушение правила, можешь мягко обратить внимание.

Примеры:

«Братан, аккуратнее 😄 За такое у нас по правилам могут наказать.»

«Не, так лучше не надо. Такое у нас запрещено.»

«Ты аккуратнее, а то модеры увидят и прилетит мут 😂»

«Ребят, выдохните. Личные разборки лучше в личке решить.»

«С рекламой аккуратнее — у нас такое без согласования нельзя.»

ВАЖНО:
Ты не модератор.
Ты не выносишь официальное наказание.
Ты не утверждаешь, что человек точно получит мут или бан.
Ты просто можешь предупредить.

Если нарушение сомнительное — лучше промолчи.

Не ищи нарушение в каждом сообщении.

==================================================
КОГДА НЕ НАДО ВМЕШИВАТЬСЯ
==================================================

Если люди просто общаются, спорят о Tanks Blitz,
шутят, обсуждают игру или разговаривают между собой —
не нужно вмешиваться только потому, что ты увидел спорное слово.

Не превращайся в полицейского чата.

==================================================
ТЕМЫ
==================================================

Ты можешь нормально разговаривать на разные темы.

Основная атмосфера сообщества связана с Tanks Blitz,
но не нужно постоянно возвращать разговор к игре.

Если участник говорит о жизни, мемах, музыке, фильмах,
играх или обычных бытовых вещах — можешь поддержать разговор.

==================================================
ИСТОРИЯ
==================================================

Используй историю сообщений, если она помогает понять контекст.

Понимай продолжения:
«а этот?»
«а почему?»
«и что?»
«ну и?»
«а если?»
«ты серьёзно?»

Не вытаскивай старые темы без причины.

Не придумывай личные факты о людях.

==================================================
СОЗДАТЕЛИ
==================================================

Если спрашивают, кто тебя создал,
отвечай, что тебя создали авторы сообщества
«Бонус-коды Tanks Blitz».

Не называй OpenAI, Groq или другие технологии своими создателями.

==================================================
СТИЛЬ
==================================================

Пиши естественно.

Не используй слишком много эмодзи.

Не ставь эмодзи в каждый ответ.

Не повторяй одну мысль несколько раз.

Не используй канцелярит.

Не говори как служба поддержки.

Ты — харизматичный участник большого игрового сообщества.
"""


# =========================================================
# APP
# =========================================================

app = Flask(__name__)

groq = Groq(
    api_key=GROQ_API_KEY
)


# =========================================================
# STATE / CACHE
# =========================================================

user_names = {}

processed_events = {}

main_blocked_until = 0
backup_blocked_until = 0


# =========================================================
# CHAT ACTIVITY STATE
# =========================================================
#
# peer_id:
# {
#     "last_message": timestamp,
#     "last_auto": timestamp
# }
#
# Хранится только в оперативной памяти сервера.
# В Supabase это не записываем.
#
# =========================================================

chat_activity = {}

activity_lock = threading.Lock()


# =========================================================
# AUTO CHAT PHRASES
# =========================================================

SILENCE_MESSAGES = [
    "Эээ, народ, вы куда все пропали? 😂 Тут чат вообще живой или я один остался?",

    "Так, а где все танкисты? Уже 20 минут тишины — подозрительно 🤨",

    "Бонус-коды Tanks Blitz сегодня решили превратиться в Бонус-коды тишины 😂",

    "Народ, вы там живые вообще? А то я уже начал разговаривать сам с собой.",

    "Что-то подозрительно тихо стало. Все ушли катать или чат объявил режим невидимки? 😂",

    "Так, перекличка. Кто-нибудь ещё существует в этом чате? 👀",

    "20 минут тишины... Всё, официально объявляю поиск пропавших танкистов 😂",

    "Народ, ну и где движ? Я уже успел соскучиться по вашему хаосу.",

    "Тишина какая-то слишком подозрительная. Кто-нибудь запустите чат обратно 😂",

    "Так, народ, чего притихли? Или все одновременно пошли делать победный бой?"
]


# =========================================================
# EVENT PROTECTION
# =========================================================

def already_processed(event_id):
    if not event_id:
        return False

    now = time.time()

    old = [
        key
        for key, saved in processed_events.items()
        if now - saved > EVENT_CACHE_TIME
    ]

    for key in old:
        processed_events.pop(
            key,
            None
        )

    if event_id in processed_events:
        return True

    processed_events[event_id] = now

    if len(processed_events) > EVENT_CACHE_LIMIT:
        oldest = min(
            processed_events,
            key=processed_events.get
        )

        processed_events.pop(
            oldest,
            None
        )

    return False


# =========================================================
# RATE LIMIT
# =========================================================

def is_rate_limit_error(error):
    text = str(error).lower()

    return any(
        x in text
        for x in (
            "429",
            "rate limit",
            "rate_limit_exceeded",
            "tokens per day",
            "tpd",
            "too many requests"
        )
    )


def get_retry_seconds(error, default):
    text = str(error)

    match = re.search(
        r"try again in\s+"
        r"(?:(\d+)h)?"
        r"(?:(\d+)m)?"
        r"(?:(\d+(?:\.\d+)?)s)?",
        text,
        re.I
    )

    if not match:
        return default

    hours = int(
        match.group(1) or 0
    )

    minutes = int(
        match.group(2) or 0
    )

    seconds = float(
        match.group(3) or 0
    )

    total = (
        hours * 3600
        + minutes * 60
        + seconds
    )

    if total <= 0:
        return default

    return int(total) + 10


# =========================================================
# GREETINGS
# =========================================================

GREETINGS = {
    "привет",
    "привет!",
    "приветик",
    "здарова",
    "здорово",
    "дарова",
    "хай",
    "хелло",
    "hello",
    "hi",
    "ку",
    "ку!"
}


SPECIAL_GREETINGS = {
    "доброе утро":
        "Доброе утро! ☀️ Народ, всем удачных боёв.",

    "добрый день":
        "Добрый день, народ 😎",

    "добрый вечер":
        "Добрый вечер! Ну что, как день прошёл?",

    "доброй ночи":
        "Доброй ночи, танкисты. Завтра продолжим разносить рандом 🌙"
}


def is_greeting(text):
    text = text.lower().strip()

    return (
        text in GREETINGS
        or text in SPECIAL_GREETINGS
    )


def greeting_response(text):
    text = text.lower().strip()

    if text in SPECIAL_GREETINGS:
        return SPECIAL_GREETINGS[text]

    responses = [
        "Привет 👋 Как жизнь?",
        "О, народ подтягивается 😎 Привет!",
        "Привет! Ну что, как оно?",
        "Здарова 😎",
        "Ку! Что тут у нас происходит?"
    ]

    return random.choice(
        responses
    )


# =========================================================
# LOCAL ROUTER
# =========================================================

QUESTION_WORDS = {
    "кто",
    "что",
    "где",
    "когда",
    "зачем",
    "почему",
    "как",
    "какой",
    "какая",
    "какие",
    "какое",
    "какого",
    "какую",
    "каких",
    "сколько",
    "куда",
    "откуда",
    "можно",
    "нужно",
    "стоит",
    "будет",
    "есть",
    "подскажешь",
    "посоветуешь",
    "скажешь",
    "знаешь",
    "думаешь"
}


FOLLOWUPS = (
    "а почему",
    "а зачем",
    "а как",
    "а какой",
    "а какая",
    "а какие",
    "а какое",
    "а где",
    "а когда",
    "а сколько",
    "а этот",
    "а эта",
    "а эти",
    "а оно",
    "а он",
    "а она",
    "а там",
    "а на",
    "а если",
    "а что если",
    "а можно",
    "а нужно",
    "а стоит",
    "и что",
    "и как",
    "и какой",
    "тогда как",
    "тогда что",
    "ну а",
    "не понял",
    "не понимаю",
    "объясни",
    "расскажи",
    "подробнее",
    "почему так"
)


IGNORED = {
    "ок",
    "окей",
    "ага",
    "угу",
    "да",
    "нет",
    "лол",
    "ахах",
    "ахаха",
    "пон",
    "ясно",
    "спс",
    "спасибо",
    "благодарю",
    "+",
    "++",
    "👍",
    "👌",
    "😂",
    "🤣"
}


BOT_WORDS = (
    "бот",
    "ботик",
    "ии",
    "искусственный интеллект",
    "нейросеть",
    "чатгпт",
    "чат гпт",
    "помощник"
)


def is_noise(text):
    text = text.lower().strip()

    if not text:
        return True

    if text in IGNORED:
        return True

    if len(text) <= 2:
        return True

    if len(text) >= 7 and len(set(text)) <= 2:
        return True

    return False


def looks_like_question(text):
    text = text.lower().strip()

    if not text:
        return False

    if text.endswith(
        ("?", "?!", "!?")
    ):
        return True

    words = re.findall(
        r"[а-яёa-z0-9]+",
        text
    )

    if not words:
        return False

    if words[0] in QUESTION_WORDS:
        return True

    if any(
        text.startswith(x)
        for x in FOLLOWUPS
    ):
        return True

    if len(words) <= 8:
        return any(
            word in QUESTION_WORDS
            for word in words[:3]
        )

    return False


def is_bot_mentioned(text):
    text = text.lower().strip()

    return any(
        word in text
        for word in BOT_WORDS
    )


def is_conversational_message(text):
    """
    Небольшой набор сообщений, на которые бот
    может реагировать как обычный участник.
    """

    text = text.lower().strip()

    phrases = (
        "как жизнь",
        "как дела",
        "что делаете",
        "кто тут",
        "кто живой",
        "кому не спится",
        "есть кто",
        "народ",
        "ребят",
        "скучно",
        "мне скучно",
        "пипец",
        "жесть",
        "капец",
        "ахах",
        "ахаха",
        "лол",
    )

    return any(
        phrase in text
        for phrase in phrases
    )


def should_use_ai(text, is_reply=False):
    text = text.strip()

    if is_noise(text):
        return False

    if looks_like_question(text):
        return True

    if is_bot_mentioned(text):
        return True

    if is_reply:
        return True

    if is_conversational_message(text):
        return True

    return False


# =========================================================
# USER NAME
# =========================================================

def get_vk_user_name(user_id):
    if not user_id:
        return None

    cached = user_names.get(
        user_id
    )

    if cached:
        saved, name = cached

        if (
            time.time() - saved
            < NAME_CACHE_TIME
        ):
            return name

    try:
        response = requests.get(
            f"{VK_API}/users.get",
            params={
                "access_token": VK_TOKEN,
                "v": VK_VERSION,
                "user_ids": user_id
            },
            timeout=10
        )

        users = response.json().get(
            "response",
            []
        )

        if not users:
            return None

        user = users[0]

        first = user.get(
            "first_name",
            ""
        ).strip()

        last = user.get(
            "last_name",
            ""
        ).strip()

        name = f"{first} {last}".strip()

        if not name:
            return None

        user_names[user_id] = (
            time.time(),
            name
        )

        return name

    except Exception as e:
        print(
            "VK name error:",
            e,
            flush=True
        )

        return None


# =========================================================
# SUPABASE MEMORY
# =========================================================

def add_memory(
    memory_key,
    role,
    content
):
    if not memory_key or not content:
        return

    try:
        supabase.table(
            "bot_memory"
        ).insert({
            "user_id": str(memory_key),
            "role": role,
            "content": content
        }).execute()

        print(
            "SUPABASE MEMORY SAVE OK",
            flush=True
        )

    except Exception as e:
        print(
            "Supabase memory save error:",
            e,
            flush=True
        )


def get_memory(memory_key):
    if not memory_key:
        return []

    try:
        response = (
            supabase
            .table("bot_memory")
            .select(
                "role, content"
            )
            .eq(
                "user_id",
                str(memory_key)
            )
            .order(
                "created_at",
                desc=True
            )
            .limit(
                MEMORY_LIMIT
            )
            .execute()
        )

        rows = response.data or []

        rows.reverse()

        return rows

    except Exception as e:
        print(
            "Supabase memory load error:",
            e,
            flush=True
        )

        return []


def get_memory_key(peer_id, user_id):
    """
    Для группового чата память общая для чата.
    Для личных сообщений память остаётся персональной.

    Это позволяет боту понимать:
        «А этот?»
        «А почему?»
        «Он же вчера говорил...»
    в рамках общего разговора.
    """

    if peer_id >= GROUP_PEER_ID:
        return f"chat:{peer_id}"

    return f"user:{user_id}"


# =========================================================
# GROQ MESSAGES
# =========================================================

def build_messages(
    text,
    peer_id=None,
    user_id=None,
    user_name=None
):
    memory_key = get_memory_key(
        peer_id or 0,
        user_id
    )

    messages = [
        {
            "role": "system",
            "content": SYSTEM_PROMPT
        }
    ]

    if user_name:
        messages.append({
            "role": "system",
            "content": (
                f"Сейчас с тобой общается {user_name}. "
                "Используй имя редко и естественно."
            )
        })

    history = get_memory(
        memory_key
    )

    if history:
        messages.extend(
            history
        )

    messages.append({
        "role": "user",
        "content": text
    })

    return messages


# =========================================================
# THINK CLEANER
# =========================================================

def clean_ai_reply(reply):
    if not reply:
        return ""

    reply = re.sub(
        r"<think>.*?</think>",
        "",
        reply,
        flags=re.DOTALL
    ).strip()

    if "<think>" in reply:
        reply = (
            reply
            .split("<think>")[0]
            .strip()
        )

    reply = reply.replace(
        "</think>",
        ""
    ).strip()

    return reply


# =========================================================
# GROQ REQUEST
# =========================================================

def ask_model(
    model,
    messages
):
    completion = groq.chat.completions.create(
        model=model,
        messages=messages,
        max_tokens=GROQ_MAX_TOKENS
    )

    usage = getattr(
        completion,
        "usage",
        None
    )

    if usage:
        print(
            "Groq:",
            "model=",
            model,
            "prompt=",
            getattr(
                usage,
                "prompt_tokens",
                None
            ),
            "completion=",
            getattr(
                usage,
                "completion_tokens",
                None
            ),
            "total=",
            getattr(
                usage,
                "total_tokens",
                None
            ),
            flush=True
        )

    reply = (
        completion
        .choices[0]
        .message
        .content
    )

    if not reply:
        raise RuntimeError(
            "Groq returned empty response."
        )

    reply = clean_ai_reply(
        reply
    )

    if not reply:
        raise RuntimeError(
            "Groq returned empty cleaned response."
        )

    return reply


# =========================================================
# GROQ
# =========================================================

def ask_groq(
    text,
    peer_id=None,
    user_id=None,
    user_name=None
):
    global main_blocked_until
    global backup_blocked_until

    messages = build_messages(
        text,
        peer_id,
        user_id,
        user_name
    )

    # =====================================================
    # 20B
    # =====================================================

    if time.time() >= main_blocked_until:

        try:
            print(
                "Groq -> 20B",
                flush=True
            )

            return ask_model(
                MAIN_MODEL,
                messages
            )

        except Exception as e:

            if is_rate_limit_error(e):

                cooldown = get_retry_seconds(
                    e,
                    MAIN_DEFAULT_COOLDOWN
                )

                main_blocked_until = (
                    time.time()
                    + cooldown
                )

                print(
                    f"20B limit -> "
                    f"120B for {cooldown}s",
                    flush=True
                )

            else:
                print(
                    "20B error:",
                    e,
                    flush=True
                )

    else:

        print(
            "20B temporarily blocked",
            flush=True
        )


    # =====================================================
    # 120B
    # =====================================================

    if time.time() >= backup_blocked_until:

        try:
            print(
                "Groq -> 120B",
                flush=True
            )

            return ask_model(
                BACKUP_MODEL,
                messages
            )

        except Exception as e:

            if is_rate_limit_error(e):

                cooldown = get_retry_seconds(
                    e,
                    BACKUP_DEFAULT_COOLDOWN
                )

                backup_blocked_until = (
                    time.time()
                    + cooldown
                )

                print(
                    f"120B limit -> "
                    f"pause {cooldown}s",
                    flush=True
                )

            else:
                print(
                    "120B error:",
                    e,
                    flush=True
                )

    else:

        print(
            "120B temporarily blocked",
            flush=True
        )

    raise RuntimeError(
        "Обе модели Groq временно недоступны."
    )


# =========================================================
# AI
# =========================================================

def ask_ai(
    text,
    peer_id=None,
    user_id=None,
    user_name=None
):
    print(
        "ROUTER -> Groq",
        flush=True
    )

    return ask_groq(
        text,
        peer_id,
        user_id,
        user_name
    )


# =========================================================
# VK SEND
# =========================================================

def send_message(
    peer_id,
    text
):
    response = requests.post(
        f"{VK_API}/messages.send",

        data={
            "access_token": VK_TOKEN,
            "v": VK_VERSION,
            "peer_id": peer_id,
            "message": text,
            "random_id": random.randint(
                1,
                2_000_000_000
            )
        },

        timeout=15
    )

    result = response.json()

    if "error" in result:
        print(
            "VK send error:",
            result["error"],
            flush=True
        )

    return result


# =========================================================
# CHAT ACTIVITY
# =========================================================

def mark_chat_activity(peer_id):
    """
    Сбрасывает таймер тишины.
    """

    if peer_id < GROUP_PEER_ID:
        return

    now = time.time()

    with activity_lock:

        current = chat_activity.get(
            peer_id,
            {}
        )

        chat_activity[peer_id] = {
            "last_message":
                now,

            "last_auto":
                current.get(
                    "last_auto",
                    0
                )
        }


def mark_auto_message(peer_id):
    """
    После автоматического сообщения
    сохраняем время автоактивности.
    """

    if peer_id < GROUP_PEER_ID:
        return

    now = time.time()

    with activity_lock:

        current = chat_activity.get(
            peer_id,
            {}
        )

        chat_activity[peer_id] = {
            "last_message":
                now,

            "last_auto":
                now
        }


def send_silence_message(peer_id):
    text = random.choice(
        SILENCE_MESSAGES
    )

    print(
        f"AUTO ACTIVITY -> peer {peer_id}: {text}",
        flush=True
    )

    result = send_message(
        peer_id,
        text
    )

    if "error" not in result:
        mark_auto_message(
            peer_id
        )


def activity_worker():
    """
    Фоновый поток.

    Если в групповом чате 20 минут тишины,
    бот пишет одно случайное сообщение.
    """

    print(
        "AUTO ACTIVITY WORKER STARTED",
        flush=True
    )

    while True:

        try:

            now = time.time()

            candidates = []

            with activity_lock:

                for peer_id, state in list(
                    chat_activity.items()
                ):

                    last_message = state.get(
                        "last_message",
                        0
                    )

                    last_auto = state.get(
                        "last_auto",
                        0
                    )

                    if not last_message:
                        continue

                    silence_time = (
                        now
                        - last_message
                    )

                    since_auto = (
                        now
                        - last_auto
                    )

                    if (
                        silence_time
                        >= SILENCE_SECONDS
                        and since_auto
                        >= AUTO_MESSAGE_COOLDOWN
                    ):

                        candidates.append(
                            peer_id
                        )

            for peer_id in candidates:

                try:
                    send_silence_message(
                        peer_id
                    )

                except Exception as e:

                    print(
                        "Auto activity send error:",
                        e,
                        flush=True
                    )

            time.sleep(
                ACTIVITY_CHECK_INTERVAL
            )

        except Exception as e:

            print(
                "Activity worker error:",
                e,
                flush=True
            )

            time.sleep(
                ACTIVITY_CHECK_INTERVAL
            )


# =========================================================
# VOICE
# =========================================================

def get_voice(message):

    for attachment in message.get(
        "attachments",
        []
    ):

        if attachment.get(
            "type"
        ) != "audio_message":
            continue

        audio = attachment.get(
            "audio_message",
            {}
        )

        transcript = audio.get(
            "transcript"
        )

        if transcript:

            return {
                "text":
                    transcript.strip(),

                "url":
                    None
            }

        url = audio.get(
            "link_ogg"
        )

        if url:

            return {
                "text":
                    None,

                "url":
                    url
            }

    return None


def transcribe_voice(url):

    path = None

    try:

        response = requests.get(
            url,
            timeout=30
        )

        response.raise_for_status()

        data = response.content

        temp = tempfile.NamedTemporaryFile(
            delete=False,
            suffix=".ogg"
        )

        path = temp.name

        with temp:
            temp.write(data)

        with open(
            path,
            "rb"
        ) as file:

            result = (
                groq
                .audio
                .transcriptions
                .create(
                    file=file,
                    model=WHISPER_MODEL,
                    response_format="text"
                )
            )

        return str(
            result
        ).strip()

    finally:

        if path:

            try:

                if os.path.exists(
                    path
                ):

                    os.remove(
                        path
                    )

                    print(
                        "Temporary voice file deleted.",
                        flush=True
                    )

            except Exception as e:

                print(
                    "Voice temp file delete error:",
                    e,
                    flush=True
                )


# =========================================================
# ERROR
# =========================================================

def ai_error_message(error):

    if (
        "обе модели"
        in str(error).lower()
    ):

        return (
            "ИИ сейчас упёрся в лимит 😅 "
            "Попробуй немного позже."
        )

    return (
        "Что-то мой цифровой мозг сейчас чихнул 😂 "
        "Попробуй ещё раз."
    )


# =========================================================
# CALLBACK
# =========================================================

@app.route(
    "/callback",
    methods=["POST"]
)
def callback():

    try:

        data = request.get_json(
            force=True
        )

        # =================================================
        # SECRET
        # =================================================

        if (
            VK_GROUP_SECRET
            and data.get("secret")
            != VK_GROUP_SECRET
        ):

            return (
                "invalid secret",
                403
            )


        event_type = data.get(
            "type"
        )


        # =================================================
        # CONFIRMATION
        # =================================================

        if event_type == "confirmation":
            return VK_CONFIRMATION_CODE


        if event_type != "message_new":
            return "ok"


        # =================================================
        # DUPLICATE
        # =================================================

        if already_processed(
            data.get("event_id")
        ):

            print(
                "Duplicate VK event.",
                flush=True
            )

            return "ok"


        message = (
            data["object"]["message"]
        )

        peer_id = int(
            message["peer_id"]
        )

        sender_id = message.get(
            "from_id"
        )

        if not sender_id:

            sender_id = message.get(
                "user_id"
            )

        user_id = str(
            sender_id
            or peer_id
        )

        text = message.get(
            "text",
            ""
        ).strip()


        # =================================================
        # ACTIVITY
        # =================================================

        # Любое сообщение человека сбрасывает
        # таймер тишины.
        mark_chat_activity(
            peer_id
        )


        # =================================================
        # BOT'S OWN MESSAGES
        # =================================================

        # VK обычно не присылает собственные сообщения
        # обратно как обычный message_new, но дополнительная
        # защита не помешает.
        if sender_id == 0:
            return "ok"


        # =================================================
        # GREETING
        # =================================================

        if is_greeting(text):

            reply = greeting_response(
                text
            )

            send_message(
                peer_id,
                reply
            )

            return "ok"


        # =================================================
        # VOICE
        # =================================================

        voice = get_voice(
            message
        )

        if voice:

            if voice["text"]:

                recognized = voice["text"]

                print(
                    "VK transcript:",
                    recognized,
                    flush=True
                )

            else:

                print(
                    "Whisper transcription...",
                    flush=True
                )

                recognized = transcribe_voice(
                    voice["url"]
                )


            if not recognized:
                return "ok"


            is_reply = bool(
                message.get(
                    "reply_message"
                )
            )

            if not should_use_ai(
                recognized,
                is_reply=is_reply
            ):

                print(
                    "Voice ignored by local router.",
                    flush=True
                )

                return "ok"


            user_name = get_vk_user_name(
                sender_id
            )


            try:

                reply = ask_ai(
                    recognized,
                    peer_id,
                    user_id,
                    user_name
                )

            except Exception as e:

                print(
                    "AI error:",
                    e,
                    flush=True
                )

                send_message(
                    peer_id,
                    ai_error_message(e)
                )

                return "ok"


            memory_key = get_memory_key(
                peer_id,
                user_id
            )


            # Голос сохраняем только как текст.
            add_memory(
                memory_key,
                "user",
                recognized
            )

            add_memory(
                memory_key,
                "assistant",
                reply
            )


            send_message(
                peer_id,
                reply
            )

            return "ok"


        # =================================================
        # IMAGES
        # =================================================
        #
        # Vision отключён.
        # Фото не отправляются в AI.
        # Фото не сохраняются в память.
        #
        # =================================================


        # =================================================
        # EMPTY
        # =================================================

        if not text:
            return "ok"


        # =================================================
        # REPLY DETECTION
        # =================================================

        is_reply = bool(
            message.get(
                "reply_message"
            )
        )


        # =================================================
        # LOCAL ROUTER
        # =================================================

        if not should_use_ai(
            text,
            is_reply=is_reply
        ):

            print(
                "Ignored locally -> 0 AI tokens.",
                flush=True
            )

            return "ok"


        # =================================================
        # NAME
        # =================================================

        user_name = get_vk_user_name(
            sender_id
        )

        if user_name:

            print(
                "User:",
                user_name,
                flush=True
            )


        # =================================================
        # AI
        # =================================================

        try:

            reply = ask_ai(
                text,
                peer_id,
                user_id,
                user_name
            )

        except Exception as e:

            print(
                "AI error:",
                e,
                flush=True
            )

            send_message(
                peer_id,
                ai_error_message(e)
            )

            return "ok"


        # =================================================
        # MEMORY
        # =================================================

        memory_key = get_memory_key(
            peer_id,
            user_id
        )

        add_memory(
            memory_key,
            "user",
            text
        )

        add_memory(
            memory_key,
            "assistant",
            reply
        )


        # =================================================
        # SEND
        # =================================================

        send_message(
            peer_id,
            reply
        )

        return "ok"


    except Exception as e:

        print(
            "Callback error:",
            e,
            flush=True
        )

        return "ok"


# =========================================================
# START
# =========================================================

if __name__ == "__main__":

    port = int(
        os.environ.get(
            "PORT",
            5000
        )
    )


    # =====================================================
    # AUTO ACTIVITY THREAD
    # =====================================================

    activity_thread = threading.Thread(
        target=activity_worker,
        daemon=True
    )

    activity_thread.start()


    print(
        "========================================",
        flush=True
    )

    print(
        "CHAT BOT MODE ENABLED",
        flush=True
    )

    print(
        "Role: харизматичный участник",
        flush=True
    )

    print(
        "Tank database: DISABLED",
        flush=True
    )

    print(
        "Sonar: DISABLED",
        flush=True
    )

    print(
        "Vision: DISABLED",
        flush=True
    )

    print(
        f"Silence auto-message: {SILENCE_MINUTES} min",
        flush=True
    )

    print(
        f"Memory limit: {MEMORY_LIMIT}",
        flush=True
    )

    print(
        f"Main model: {MAIN_MODEL}",
        flush=True
    )

    print(
        f"Backup model: {BACKUP_MODEL}",
        flush=True
    )

    print(
        "========================================",
        flush=True
    )


    app.run(
        host="0.0.0.0",
        port=port,
        threaded=True
    )
