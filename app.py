import os
import json
import requests
from flask import Flask, request, abort
from collections import deque

app = Flask(__name__)

# Render environment variables
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
GROUP_ID  = os.getenv("GROUP_ID", "")
TV_SECRET = os.getenv("TV_SECRET", "")
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

# ── Signal queue for MT5 EA to poll ──
signal_queue = deque(maxlen=50)

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

EVENT_MAP = {
    "🟢 BUY GOLD NOW 🟢":  ("🟢", "BUY GOLD NOW", None),
    "🔴 SELL GOLD NOW 🔴": ("🔴", "SELL GOLD NOW", None),
    "📈 TREND UP 📈":      ("📈", "TREND UP",   None),
    "📉 TREND DOWN 📉":    ("📉", "TREND DOWN", None),
    "🔵 TP1 HIT 🔵":       ("🔵", "TP1 HIT",    "Take partial"),
    "🔥 TP2 HIT 🔥":       ("🔥", "TP2 HIT",    "Full take profit"),
    "🚀 TP3 HIT 🚀":       ("🚀", "TP3 HIT",    "Let it run"),
    "❌ SL HIT ❌":         ("❌", "STOP LOSS HIT", None),
    "🧪 TEST SIGNAL — DO NOT FOLLOW 🧪": (
        "🧪", "TEST SIGNAL", "⚠️ THIS IS A TEST — DO NOT FOLLOW ⚠️",
    ),
    "🧪 TEST": ("🧪", "TEST", "⚠️ THIS IS A TEST — DO NOT FOLLOW ⚠️"),
}

# Map event strings to MT5 action codes
ACTION_MAP = {
    "🟢 BUY GOLD NOW 🟢":  "BUY",
    "🔴 SELL GOLD NOW 🔴": "SELL",
    "🔵 TP1 HIT 🔵":       "TP1",
    "🔥 TP2 HIT 🔥":       "TP2",
    "🚀 TP3 HIT 🚀":       "TP3",
    "❌ SL HIT ❌":         "SL",
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

    event   = data.get("event", "ALERT")
    ticker  = data.get("ticker", "")
    tf      = data.get("tf", "")
    price   = data.get("price", "")
    tp1     = data.get("tp1", "")
    tp2     = data.get("tp2", "")
    tp3     = data.get("tp3", "")
    sl      = data.get("sl", "")
    partial = data.get("partial", "")

    emoji, pretty_event, tagline = EVENT_MAP.get(event, ("🔔", event, None))

    # ── Send to Telegram ──
    lines = [f"{emoji} {pretty_event}"]
    if tagline:
        lines.append(tagline)
    lines.append(f"{ticker} • {tf}")
    lines.append(f"Price: {price}")
    if tp1 or tp2 or tp3:
        if tp1: lines.append(f"TP1: {tp1}")
        if tp2: lines.append(f"TP2: {tp2}")
        if tp3: lines.append(f"TP3: {tp3}")
        if sl:  lines.append(f"SL: {sl}")
        lines.append("")
        lines.append("⚠️ Please trade carefully scalping ⚠️")
    msg = "\n".join(lines)
    send_to_group(msg)

    # ── Queue signal for MT5 EA ──
    action = ACTION_MAP.get(event)
    if action:
        signal = {
            "action":  action,
            "ticker":  ticker,
            "tf":      tf,
            "price":   float(price)   if price   else 0,
            "tp1":     float(tp1)     if tp1     else 0,
            "tp2":     float(tp2)     if tp2     else 0,
            "tp3":     float(tp3)     if tp3     else 0,
            "sl":      float(sl)      if sl      else 0,
            "partial": float(partial) if partial else 0,
            "event":   event,
        }
        signal_queue.append(signal)
        print(f"QUEUED SIGNAL: {signal}")

    return {"status": "sent"}, 200

# ── MT5 EA polls this every 2 seconds ──
@app.get("/poll")
def poll():
    signals = list(signal_queue)
    signal_queue.clear()
    return {"count": len(signals), "signals": signals}, 200
