import os
import json
import requests
from flask import Flask, request, abort, jsonify

app = Flask(__name__)

# ── Render environment variables ─────────────────────────────────────
BOT_TOKEN  = os.getenv("BOT_TOKEN", "")
GROUP_ID   = os.getenv("GROUP_ID", "")
TV_SECRET  = os.getenv("TV_SECRET", "")
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

# ── MT5 signal queue ─────────────────────────────────────────────────
# Signals are added here when TradingView fires an alert.
# MT5 EA polls /poll every 2 seconds to pick them up.
signal_queue   = []   # pending signals not yet delivered to MT5
signal_history = []   # last 200 signals for debugging

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
EVENT_MAP = {
    # Entries
    "🟢 BUY GOLD NOW 🟢":  ("🟢", "BUY GOLD NOW", None),
    "🔴 SELL GOLD NOW 🔴": ("🔴", "SELL GOLD NOW", None),
    # Trend
    "📈 TREND UP 📈":      ("📈", "TREND UP",   None),
    "📉 TREND DOWN 📉":    ("📉", "TREND DOWN", None),
    # TP / SL hits
    "🔵 Partial HIT Make Risk Free 🔵": ("🔵", "Partial HIT Make Risk Free", None),
    "🔥 TP1 HIT 🔥":       ("🔥", "TP1 HIT",    None),
    "🚀 TP2 HIT TO THE MOON!!🚀": ("🚀", "TP2 HIT TO THE MOON!!", None),
    "💎🙌 TP3 HIT 500 pips!! 💎🙌 diamond hands": ("💎🙌", "TP3 HIT 500 pips!! diamond hands", None),
    "❌ SL HIT ❌":         ("❌", "STOP LOSS HIT", None),
    # EMA indicator entries
    "🟢 BUY GOLD NOW":     ("🟢", "BUY GOLD NOW", None),
    "🔴 SELL GOLD NOW":    ("🔴", "SELL GOLD NOW", None),
    "🔔 💥 TREND REVERSAL BUY GOLD NOW 💥":  ("🔔💥", "TREND REVERSAL BUY GOLD NOW", None),
    "🔔 💥 TREND REVERSAL SELL GOLD NOW 💥": ("🔔💥", "TREND REVERSAL SELL GOLD NOW", None),
    # Test
    "🧪 TEST SIGNAL — DO NOT FOLLOW 🧪": (
        "🧪", "TEST SIGNAL", "⚠️ THIS IS A TEST — DO NOT FOLLOW ⚠️",
    ),
    "🧪 TEST": ("🧪", "TEST", "⚠️ THIS IS A TEST — DO NOT FOLLOW ⚠️"),
}

def parse_action(event: str) -> str:
    """Convert event string to MT5 action keyword."""
    e = event.lower()
    if "buy" in e and "trend" not in e:  return "BUY"
    if "sell" in e and "trend" not in e: return "SELL"
    if "trend reversal buy"  in e:       return "BUY"
    if "trend reversal sell" in e:       return "SELL"
    if "partial" in e:                   return "PARTIAL"
    if "tp1" in e:                       return "TP1"
    if "tp2" in e:                       return "TP2"
    if "tp3" in e or "diamond" in e:     return "TP3"
    if "sl hit" in e or "stop loss" in e:return "SL"
    if "trend up" in e:                  return "TREND_UP"
    if "trend down" in e:                return "TREND_DOWN"
    return "INFO"

# ── MAIN WEBHOOK — receives TradingView alerts ────────────────────────
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

    event  = data.get("event",  "ALERT")
    ticker = data.get("ticker", "")
    tf     = data.get("tf",     "")
    price  = data.get("price",  "")
    # Support both capitalised and lowercase TP/SL key names
    partial= data.get("Partial", data.get("partial", ""))
    tp1    = data.get("tp1",    "")
    tp2    = data.get("tp2",    "")
    tp3    = data.get("tp3",    "")
    sl     = data.get("sl",     "")

    # ── 1. Send to Telegram (unchanged from your original) ────────────
    emoji, pretty_event, tagline = EVENT_MAP.get(event, ("🔔", event, None))
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

    # ── 2. Queue signal for MT5 EA ────────────────────────────────────
    action = parse_action(event)
    signal = {
        "id"        : __import__("time").time_ns(),
        "action"    : action,
        "event"     : event,
        "ticker"    : ticker,
        "tf"        : tf,
        "price"     : float(price)   if price   else 0.0,
        "partial"   : float(partial) if partial else None,
        "tp1"       : float(tp1)     if tp1     else None,
        "tp2"       : float(tp2)     if tp2     else None,
        "tp3"       : float(tp3)     if tp3     else None,
        "sl"        : float(sl)      if sl      else None,
        "delivered" : False,
    }

    # Only queue actionable signals
    if action in ("BUY", "SELL", "PARTIAL", "TP1", "TP2", "TP3", "SL"):
        signal_queue.append(signal)

    # Keep history
    signal_history.insert(0, signal)
    if len(signal_history) > 200:
        signal_history.pop()

    print(f"SIGNAL QUEUED: {action} {ticker} tf={tf} price={price}")
    return {"status": "sent"}, 200


# ── MT5 POLL ENDPOINT — EA calls this every 2 seconds ────────────────
@app.get("/poll")
def poll():
    """MT5 EA polls this endpoint to collect pending signals."""
    pending = [s for s in signal_queue if not s["delivered"]]

    # Mark as delivered
    for s in pending:
        s["delivered"] = True

    # Clean up old delivered signals, keep last 50
    delivered = [s for s in signal_queue if s["delivered"]]
    undelivered = [s for s in signal_queue if not s["delivered"]]
    signal_queue.clear()
    signal_queue.extend(undelivered + delivered[-50:])

    return jsonify({"signals": pending, "count": len(pending)})


# ── STATUS PAGE ───────────────────────────────────────────────────────
@app.get("/status")
def status():
    pending = len([s for s in signal_queue if not s["delivered"]])
    return jsonify({
        "status"  : "running",
        "pending" : pending,
        "history" : signal_history[:20]
    })
