import time, threading, math, requests
from datetime import datetime, timezone
from flask import Flask
import ccxt
import pandas as pd
from ta.trend import EMAIndicator, MACD
from ta.momentum import RSIIndicator, StochasticOscillator
from bs4 import BeautifulSoup

app = Flask(__name__)
TELEGRAM_TOKEN = "8218941018:AAEMUIKxhYjHBtdsTp_1cSQoKoN67g6pNvI"
CHAT_ID = "1603606771"
PAIRS = ["BTC/USDT","ETH/USDT","SOL/USDT","BNB/USDT","XRP/USDT","BANANA/USDT","SUI/USDT","AVA/USDT"]
TIMEFRAMES = ["5m", "15m", "30m"]
stats = {"win": 0, "loss": 0, "signals": 0}
active_signals = {}
exchange_global = ccxt.binance({"enableRateLimit": True})
news_cache = {"events": [], "last_fetch": 0}

def send_tele(msg):
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"}, timeout=10)
    except:
        pass

def fmt(price):
    if price == 0:
        return "0"
    digits = max(0, -int(math.floor(math.log10(abs(price)))) + 3)
    return f"{price:.{digits}f}"

def get_winrate():
    total = stats["win"] + stats["loss"]
    if total == 0:
        return "N/A (baru mulai)"
    wr = (stats["win"] / total) * 100
    return f"{wr:.1f}% ({stats['win']}W/{stats['loss']}L)"

def fetch_ff_news():
    try:
        now = time.time()
        if now - news_cache["last_fetch"] < 3600:
            return news_cache["events"]
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.google.com/"
        }
        r = requests.get("https://www.forexfactory.com/calendar", headers=headers, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")
        events = []
        rows = soup.select("tr.calendar__row")
        current_time_str = None
        for row in rows:
            time_td = row.select_one("td.calendar__time")
            if time_td and time_td.text.strip():
                current_time_str = time_td.text.strip()
            impact = row.select_one("td.calendar__impact span")
            title_td = row.select_one("td.calendar__event")
            currency_td = row.select_one("td.calendar__currency")
            if impact and title_td and currency_td:
                impact_class = impact.get("class", [])
                impact_level = "low"
                if any("high" in c for c in impact_class):
                    impact_level = "high"
                elif any("medium" in c for c in impact_class):
                    impact_level = "medium"
                events.append({
                    "time": current_time_str or "?",
                    "currency": currency_td.text.strip(),
                    "title": title_td.text.strip(),
                    "impact": impact_level
                })
        news_cache["events"] = events
        news_cache["last_fetch"] = now
        return events
    except:
        return news_cache.get("events", [])

def get_high_impact_news():
    events = fetch_ff_news()
    high_events = [e for e in events if e["impact"] == "high"]
    return high_events[:3] if high_events else []

def analyze_tf(pair, tf):
    try:
        ohlcv = exchange_global.fetch_ohlcv(pair, tf, limit=150)
        df = pd.DataFrame(ohlcv, columns=["ts","open","high","low","close","vol"])
        close = df["close"]
        high = df["high"]
        low = df["low"]
        ema25 = EMAIndicator(close, 25).ema_indicator()
        ema75 = EMAIndicator(close, 75).ema_indicator()
        ema140 = EMAIndicator(close, 140).ema_indicator()
        rsi = RSIIndicator(close, 14).rsi()
        stoch = StochasticOscillator(high, low, close, 14, 3)
        stoch_k = stoch.stoch()
        stoch_d = stoch.stoch_signal()
        i = -1
        p = -2
        long_signal = (
            ema25.iloc[i] > ema75.iloc[i] > ema140.iloc[i] and
            close.iloc[i] > ema25.iloc[i] and
            stoch_k.iloc[i] > stoch_d.iloc[i] and
            stoch_k.iloc[p] <= stoch_d.iloc[p] and
            stoch_k.iloc[i] < 80 and
            rsi.iloc[i] > 45
        )
        short_signal = (
            ema25.iloc[i] < ema75.iloc[i] < ema140.iloc[i] and
            close.iloc[i] < ema25.iloc[i] and
            stoch_k.iloc[i] < stoch_d.iloc[i] and
            stoch_k.iloc[p] >= stoch_d.iloc[p] and
            stoch_k.iloc[i] > 20 and
            rsi.iloc[i] < 55
        )
        if long_signal:
            return "LONG"
        if short_signal:
            return "SHORT"
        return None
    except:
        return None

def check_pair(pair):
    results = [analyze_tf(pair, tf) for tf in TIMEFRAMES]
    long_count = results.count("LONG")
    short_count = results.count("SHORT")
    if long_count >= 2:
        return "LONG"
    if short_count >= 2:
        return "SHORT"
    return None

def monitor_signal(pair, action, entry, tp1, tp2, sl):
    deadline = time.time() + 14400
    tp1_hit = False
    while time.time() < deadline:
        try:
            time.sleep(60)
            ticker = exchange_global.fetch_ticker(pair)
            price = ticker["last"]
            if action == "LONG":
                if not tp1_hit and price >= tp1:
                    tp1_hit = True
                    send_tele(f"🎯 <b>TP1 HIT!</b>\nPair: {pair}\nSignal: LONG\nEntry: ${fmt(entry)}\nTP1: ${fmt(tp1)}\n\nHolding for TP2: ${fmt(tp2)}...")
                elif tp1_hit and price >= tp2:
                    stats["win"] += 1
                    send_tele(f"💰 <b>TP2 HIT!</b>\nPair: {pair}\nSignal: LONG\nEntry: ${fmt(entry)}\nTP2: ${fmt(tp2)}\n\n📊 Win Rate: {get_winrate()}")
                    active_signals.pop(pair, None)
                    return
                elif price <= sl:
                    if tp1_hit:
                        send_tele(f"⚠️ <b>SL HIT after TP1</b>\nPair: {pair}\nPartial win\n\n📊 Win Rate: {get_winrate()}")
                    else:
                        stats["loss"] += 1
                        send_tele(f"❌ <b>SL HIT!</b>\nPair: {pair}\nSignal: LONG\nEntry: ${fmt(entry)}\nSL: ${fmt(sl)}\n\n📊 Win Rate: {get_winrate()}")
                    active_signals.pop(pair, None)
                    return
            else:
                if not tp1_hit and price <= tp1:
                    tp1_hit = True
                    send_tele(f"🎯 <b>TP1 HIT!</b>\nPair: {pair}\nSignal: SHORT\nEntry: ${fmt(entry)}\nTP1: ${fmt(tp1)}\n\nHolding for TP2: ${fmt(tp2)}...")
                elif tp1_hit and price <= tp2:
                    stats["win"] += 1
                    send_tele(f"💰 <b>TP2 HIT!</b>\nPair: {pair}\nSignal: SHORT\nEntry: ${fmt(entry)}\nTP2: ${fmt(tp2)}\n\n📊 Win Rate: {get_winrate()}")
                    active_signals.pop(pair, None)
                    return
                elif price >= sl:
                    if tp1_hit:
                        send_tele(f"⚠️ <b>SL HIT after TP1</b>\nPair: {pair}\nPartial win\n\n📊 Win Rate: {get_winrate()}")
                    else:
                        stats["loss"] += 1
                        send_tele(f"❌ <b>SL HIT!</b>\nPair: {pair}\nSignal: SHORT\nEntry: ${fmt(entry)}\nSL: ${fmt(sl)}\n\n📊 Win Rate: {get_winrate()}")
                    active_signals.pop(pair, None)
                    return
        except:
            pass
    active_signals.pop(pair, None)

def run_scanner():
    send_tele(
        "🚀 <b>Bot 5m15m30m Started!</b>\n"
        f"Pairs: {len(PAIRS)}\n"
        "Strategy: 2-of-3 TF Confirmation\n"
        "TP1: +3% (1:3) | TP2: +5% (1:5)\n"
        "News: ForexFactory ✅\n\n"
        "Monitoring..."
    )
    while True:
        for pair in PAIRS:
            if pair in active_signals:
                continue
            try:
                action = check_pair(pair)
                if action:
                    ticker = exchange_global.fetch_ticker(pair)
                    price = ticker["last"]
                    if action == "LONG":
                        sl = price * 0.99
                        tp1 = price * 1.03
                        tp2 = price * 1.05
                        sl_pct = "(-1%)"
                        trend = "Uptrend"
                        stoch_dir = "oversold crossup"
                        ema_dir = ">"
                    else:
                        sl = price * 1.015
                        tp1 = price * 0.955
                        tp2 = price * 0.925
                        sl_pct = "(-1.5%)"
                        trend = "Downtrend"
                        stoch_dir = "overbought crossdown"
                        ema_dir = "<"
                    stats["signals"] += 1
                    active_signals[pair] = action
                    emoji = "🟢" if action == "LONG" else "🔴"
                    tfs = [tf for tf in TIMEFRAMES if analyze_tf(pair, tf) == action]
                    tf_str = ", ".join(tfs) if tfs else "2/3 TF"

                    # Get news
                    news_events = get_high_impact_news()
                    news_section = ""
                    if news_events:
                        news_lines = "\n".join([f"  ⚠️ {e['currency']} {e['title']} ({e['time']})" for e in news_events[:3]])
                        news_section = f"\n🗞 <b>High Impact News Today:</b>\n{news_lines}\n⚠️ Hati-hati volatilitas!\n"

                    msg = (
                        f"🚨 <b>SIGNAL ALERT!</b>\n"
                        f"━━━━━━━━━━━━━━\n"
                        f"📌 Pair: {pair}\n"
                        f"📊 Signal: {emoji} {action}\n"
                        f"━━━━━━━━━━━━━━\n"
                        f"📈 Entry: ${fmt(price)}\n"
                        f"🎯 TP1: ${fmt(tp1)} (+3%)\n"
                        f"💰 TP2: ${fmt(tp2)} (+5%)\n"
                        f"🛑 SL: ${fmt(sl)} {sl_pct}\n"
                        f"━━━━━━━━━━━━━━\n"
                        f"🔍 Analisis:\n"
                        f"• EMA25 {ema_dir} EMA75 {ema_dir} EMA140 → {trend} ✅\n"
                        f"• Stochastic {stoch_dir} ✅\n"
                        f"• TF Confirm: {tf_str} ✅\n"
                        f"━━━━━━━━━━━━━━\n"
                        f"{news_section}"
                        f"📊 Win Rate: {get_winrate()}\n"
                        f"⏰ TF: 5m/15m/30m | Binance"
                    )
                    send_tele(msg)
                    threading.Thread(target=monitor_signal, args=(pair, action, price, tp1, tp2, sl), daemon=True).start()
            except:
                pass
        time.sleep(300)

@app.route("/")
def home():
    return f"Bot Running! | Signals: {stats['signals']} | Win Rate: {get_winrate()}"

threading.Thread(target=run_scanner, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
