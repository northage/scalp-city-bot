import os
import json
import requests
from flask import Flask, request, abort

app = Flask(__name__)

# Render environment variables
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
GROUP_ID = os.getenv("GROUP_ID", "")
TV_SECRET = os.getenv("TV_SECRET", "")
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"


def send_to_group(text: str) -> None:
    if not BOT_TOKEN or not GROUP_ID:
        print("ERROR: Missing BOT_TOKEN or GROUP_ID environment variable")
        return
    r = requests.post(
        f"{TELEGRAM_API}/sendMessage",
        json={"chat_id": GROUP_ID, "text": text},
        timeout=20,
    )
    print("TELEGRAM STATUS:", r.status_code)
    print("TELEGRAM BODY:", r.text)
    r.raise_for_status()


@app.get("/")
def health():
    return "OK", 200


# event -> (emoji, pretty title, optional tagline)
# These keys must match the `event` strings sent by the indicator's
# f_payload(...) calls exactly.
EVENT_MAP = {
    # Entries (full TP/SL ladder shown)
    "🟢 BUY GOLD NOW 🟢":  ("🟢", "BUY GOLD NOW", None),
    "🔴 SELL GOLD NOW 🔴": ("🔴", "SELL GOLD NOW", None),
    # Trend
    "📈 TREND UP 📈":      ("📈", "TREND UP",   None),
    "📉 TREND DOWN 📉":    ("📉", "TREND DOWN", None),
    # TP / SL hits
    "🔵 TP1 HIT 🔵":       ("🔵", "TP1 HIT",    "Take partial"),
    "🔥 TP2 HIT 🔥":       ("🔥", "TP2 HIT",    "Full take profit"),
    "🚀 TP3 HIT 🚀":       ("🚀", "TP3 HIT",    "Let it run"),
    "❌ SL HIT ❌":         ("❌", "STOP LOSS HIT", None),
    # Manual test (full ladder, but clearly flagged as a test)
    "🧪 TEST SIGNAL — DO NOT FOLLOW 🧪": (
        "🧪",
        "TEST SIGNAL",
        "⚠️ THIS IS A TEST — DO NOT FOLLOW ⚠️",
    ),
    # Backwards-compat for the old short test event string
    "🧪 TEST": ("🧪", "TEST", "⚠️ THIS IS A TEST — DO NOT FOLLOW ⚠️"),
}


@app.post("/tv")
def tradingview_webhook():
    raw = request.get_data(as_text=True) or ""
    try:
        data = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        data = {}

    # Secret check
    if TV_SECRET:
        incoming = data.get("secret", "")
        if incoming != TV_SECRET:
            print("SECRET MISMATCH")
            abort(401)

    event = data.get("event", "ALERT")
    ticker = data.get("ticker", "")
    tf = data.get("tf", "")
    price = data.get("price", "")
    tp1 = data.get("tp1", "")
    tp2 = data.get("tp2", "")
    tp3 = data.get("tp3", "")
    sl = data.get("sl", "")

    emoji, pretty_event, tagline = EVENT_MAP.get(event, ("🔔", event, None))

    # Build the message
    lines = [f"{emoji} {pretty_event}"]
    if tagline:
        lines.append(tagline)
    lines.append(f"{ticker} • {tf}")
    lines.append(f"Price: {price}")

    # Show full TP/SL ladder ONLY on entries (when the indicator
    # actually included tp1/tp2/tp3/sl in the payload).
    if tp1 or tp2 or tp3:
        if tp1:
            lines.append(f"TP1: {tp1}")
        if tp2:
            lines.append(f"TP2: {tp2}")
        if tp3:
            lines.append(f"TP3: {tp3}")
        if sl:
            lines.append(f"SL: {sl}")
        lines.append("")
        lines.append("⚠️ Please trade carefully scalping ⚠️")

    msg = "\n".join(lines)
    send_to_group(msg)
    return {"status": "sent"}, 200
