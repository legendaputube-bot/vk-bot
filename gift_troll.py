import re, time

_sb = None
_cache = {"t": 0, "rows": []}
_pending = {}
ID_RE = re.compile(r"(?:\[id|@id|\bid)(\d{5,12})", re.I)


def init(sb):
    global _sb
    _sb = sb


def _load(force=False):
    if _sb is None:
        return []
    if not force and time.time() - _cache["t"] < 60:
        return _cache["rows"]
    try:
        rows = _sb.table("gift_cheaters").select("user_id,note").eq("active", True).execute().data or []
        _cache["rows"] = rows
        _cache["t"] = time.time()
    except Exception as e:
        print("gift_troll load error:", e, flush=True)
    return _cache["rows"]


def _note(uid):
    for r in _load():
        if str(r.get("user_id")) == str(uid):
            return r.get("note") or ""
    return None


def is_cheater(uid):
    return _note(uid) is not None


def tag(uid, name):
    return "[id%s|%s] " % (uid, name or "друг")


def troll_prompt(uid, text):
    return ("[СЛУЖЕБНО, не повторяй эту пометку: этот участник обещал в чате подарки и обманул, не отдал (" + (_note(uid) or "") + "). Ответь на его сообщение как обычно, но дерзко и с юмором подколи его за невыполненные обещания про подарки. Без угроз и без оскорблений не по теме. Его сообщение: ] " + text)


def _resolve(m):
    if m:
        return int(m.group(1))
    rows = _load(True)
    if len(rows) == 1:
        return int(rows[0]["user_id"])
    return None


def _close(uid):
    _sb.table("gift_cheaters").update({"active": False}).eq("user_id", uid).execute()
    _load(True)
    return "Принято, подарок получен, про него больше не подкалываю."


def owner_flow(text, sender_id, owner):
    if not owner or _sb is None:
        return None
    low = text.lower()
    m = ID_RE.search(text)
    got = "получил" in low and "подар" in low
    pend = _pending.pop(sender_id, None)
    if pend and time.time() - pend < 600 and not got and (m or "от " in low):
        t = _resolve(m)
        if t:
            return _close(t)
        return "Не понял, от кого. Напиши id, например @id123456."
    if got:
        t = _resolve(m)
        if t:
            return _close(t)
        if _load(True):
            _pending[sender_id] = time.time()
            return "От кого получил? Напиши id."
        return None
    if m and re.search(r"обман|не отдал|не закинул|кинул|не подарил", low):
        _sb.table("gift_cheaters").upsert({"user_id": int(m.group(1)), "note": text[:200], "active": True}).execute()
        _load(True)
        return "Записал [id%s|его] в обманщиков, буду подкалывать." % m.group(1)
    return None
