import os
import sys
import logging
from flask import request, jsonify

API_KEY = os.environ.get("BLITZ_API_KEY", "").strip()
VK_PEER = os.environ.get("BLITZ_VK_PEER", "").strip()


def register(app, ask_ai, send_message, save_chat_message):
    g = sys._getframe(1).f_globals

    @app.route("/api/ask", methods=["POST"])
    def blitz_api_ask():
        if not API_KEY or request.headers.get("X-Api-Key", "") != API_KEY:
            return jsonify({"ok": False, "error": "forbidden"}), 403
        data = request.get_json(silent=True) or {}
        text = str(data.get("text") or "").strip()[:300]
        name = str(data.get("name") or "Участник").strip()[:60]
        try:
            uid = int(data.get("user_id"))
        except Exception:
            uid = 0
        if not text or not VK_PEER or not uid:
            return jsonify({"ok": False, "error": "empty"}), 400
        chat_id = int(VK_PEER)
        import time as _t, traceback as _tb
        _t0 = _t.time()
        print(f'BRIDGE IN | uid={uid} name={name} text={text[:60]}', flush=True)
        try:
            cmd = g["handle_owner_command"](text, uid)
            if cmd:
                send_message(chat_id, cmd)
                return jsonify({"ok": True, "reply": cmd})
            if not g.get("SYSTEM_ENABLED", True):
                return jsonify({"ok": True, "reply": ""})
            save_chat_message(chat_id, uid, name, "user", text)
            try:
                g["update_bot_emotion"](chat_id, text, uid, name, True)
            except Exception as e:
                logging.error("blitz_api emotion: %s", e)
            try:
                g["save_explicit_user_memory"](chat_id, uid, name, text)
            except Exception as e:
                logging.error("blitz_api memory: %s", e)
            print('BRIDGE ask_ai start', flush=True)
            reply = ask_ai(chat_id, text, str(uid), name)
            print(f'BRIDGE REPLY | {_t.time()-_t0:.1f}s | len={len(reply or "")} | {str(reply)[:80]}', flush=True)
            if reply:
                save_chat_message(chat_id, None, "Бот", "assistant", reply)
                send_message(chat_id, reply)
            print('BRIDGE OUT ok', flush=True)
            return jsonify({"ok": True, "reply": reply or ""})
        except Exception as e:
            logging.error("blitz_api_ask: %s", e)
            print('BRIDGE ERROR |', e, flush=True)
            print(_tb.format_exc(), flush=True)
            return jsonify({"ok": False, "error": "ai"}), 500
