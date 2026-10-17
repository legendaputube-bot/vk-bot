import os
import logging
from flask import request, jsonify

API_KEY = os.environ.get("BLITZ_API_KEY", "").strip()
VK_PEER = os.environ.get("BLITZ_VK_PEER", "").strip()


def register(app, ask_ai, send_message, save_chat_message):

    @app.route("/api/ask", methods=["POST"])
    def blitz_api_ask():
        if not API_KEY or request.headers.get("X-Api-Key", "") != API_KEY:
            return jsonify({"ok": False, "error": "forbidden"}), 403
        data = request.get_json(silent=True) or {}
        text = str(data.get("text") or "").strip()[:300]
        name = str(data.get("name") or "Участник").strip()[:60]
        uid = str(data.get("user_id") or "app")
        if not text or not VK_PEER:
            return jsonify({"ok": False, "error": "empty"}), 400
        try:
            chat_id = int(VK_PEER)
            save_chat_message(chat_id, None, name, "user", text)
            reply = ask_ai(chat_id, text, uid, name)
            if reply:
                save_chat_message(chat_id, None, "Бот", "assistant", reply)
                send_message(chat_id, reply)
            return jsonify({"ok": True, "reply": reply or ""})
        except Exception as e:
            logging.error("blitz_api_ask: %s", e)
            return jsonify({"ok": False, "error": "ai"}), 500
