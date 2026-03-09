import time, threading, math, requests
from flask import Flask
import ccxt
import pandas as pd
from ta.trend import EMAIndicator, MACD
from ta.momentum import RSIIndicator, StochasticOscillator

app = Flask(__name__)
TELEGRAM_TOKEN = "8218941018:AAEMUIKxhYjHBtdsTp_1cSQoKoN67g6pNvI"
CHAT_ID = "1603606771"
PAIRS = ["BTC/USDT","ETH/USDT","SOL/USDT","BNB/USDT","XRP/USDT","BANANA/USDT","SUI/USDT","AVA/USDT"]
TIMEFRAMES = ["5m", "15m", "30m"]
stats = {"win": 0, "loss": 0, "signals": 0}
active_signals = {}
exchange_global = ccxt.binance({"enableRateLimit": True})

def send_tele(msg):
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"}, timeout=10)
    except: pass

def fmt(price):
    if price == 0: return "0"
    digits = max(0, -int(math.floor(math.log10(abs(price)))) + 3)
    return f"{price:.{digits}f}"

def get_winrate():
    total = stats["win"] + stats["loss"]
    if total == 0: return "N/A (baru mulai)"
    wr = (stats["win"] / total) * 100
    return f"{wr:.1f}% ({stats['win']}W/{stats['loss']}L)"

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
        if long_signal: return "LONG"
        if short_signal: return "SHORT"
        return None
    except: return None

def check_pair(pair):
    results = [analyze_tf(pair, tf) for tf in TIMEFRAMES]
    long_count = results.count("LONG")
    short_count = results.count("SHORT")
    if long_count >= 2: return "LONG"
    if short_count >= 2: return "SHORT"
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
                    send_tele("🎯 <b>TP1 HIT!</b>\nPair: "+pair+"\nSignal: LONG\nEntry: $"+fmt(entry)+"\nTP1: $"+fmt(tp1)+"\n\nHolding for TP2: $"+fmt(tp2)+"...")
                elif tp1_hit and price >= tp2:
                    stats["win"] += 1
                    send_tele("💰 <b>TP2 HIT!</b>\nPair: "+pair+"\nSignal: LONG\nEntry: $"+fmt(entry)+"\nTP2: $"+fmt(tp2)+"\n\n📊 Win Rate: "+get_winrate())
                    active_signals.pop(pair, None)
                    return
                elif price <= sl:
                    if tp1_hit:
                        send_tele("⚠️ <b>SL HIT after TP1</b>\nPair: "+pair+"\nSignal: LONG\nPartial win — exited near TP1\n\n📊 Win Rate: "+get_winrate())
                    else:
                        stats["loss"] += 1
                        send_tele("❌ <b>SL HIT!</b>\nPair: "+pair+"\nSignal: LONG\nEntry: $"+fmt(entry)+"\nSL: $"+fmt(sl)+"\n\n📊 Win Rate: "+get_winrate())
                    active_signals.pop(pair, None)
                    return
            else:
                if not tp1_hit and price <= tp1:
                    tp1_hit = True
                    send_tele("🎯 <b>TP1 HIT!</b>\nPair: "+pair+"\nSignal: SHORT\nEntry: $"+fmt(entry)+"\nTP1: $"+fmt(tp1)+"\n\nHolding for TP2: $"+fmt(tp2)+"...")
                elif tp1_hit and price <= tp2:
                    stats["win"] += 1
                    send_tele("💰 <b>TP2 HIT!</b>\nPair: "+pair+"\nSignal: SHORT\nEntry: $"+fmt(entry)+"\nTP2: $"+fmt(tp2)+"\n\n📊 Win Rate: "+get_winrate())
                    active_signals.pop(pair, None)
                    return
                elif price >= sl:
                    if tp1_hit:
                        send_tele("⚠️ <b>SL HIT after TP1</b>\nPair: "+pair+"\nSignal: SHORT\nPartial win — exited near TP1\n\n📊 Win Rate: "+get_winrate())
                    else:
                        stats["loss"] += 1
                        send_tele("❌ <b>SL HIT!</b>\nPair: "+pair+"\nSignal: SHORT\nEntry: $"+fmt(entry)+"\nSL: $"+fmt(sl)+"\n\n📊 Win Rate: "+get_winrate())
                    active_signals.pop(pair, None)
                    return
        except: pass
    active_signals.pop(pair, None)

def run_scanner():
    send_tele("🚀 <b>Bot 5m15m30m Started!</b>\nPairs: "+str(len(PAIRS))+"\nStrategy: 2-of-3 TF Confirmation\nTP1: 1:3 | TP2: 1:5\n\nMonitoring...")
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
                    else:
                        sl = price * 1.015
                        tp1 = price * 0.955
                        tp2 = price * 0.925
                    stats["signals"] += 1
                    active_signals[pair] = action
                    emoji = "🟢" if action == "LONG" else "🔴"
                    tfs = [tf for tf in TIMEFRAMES if analyze_tf(pair, tf) == action]
                    send_tele(
                        f"{emoji} <b>{action} SIGNAL</b>\n"
                        f"Pair: {pair}\n"
                        f"Entry: ${fmt(price)}\n"
                        f"TP1: ${fmt(tp1)} (+3%) 🎯\n"
                        f"TP2: ${fmt(tp2)} (+5%) 💰\n"
                        f"SL: ${fmt(sl)}\n"
                        f"TF Confirm: {', '.join(tfs)}\n\n"
                        f"📊 Total Signals: {stats['signals']}"
                    )
                    threading.Thread(target=monitor_signal, args=(pair, action, price, tp1, tp2, sl), daemon=True).start()
            except: pass
        time.sleep(300)

@app.route("/")
def home():
    total = stats["win"] + stats["loss"]
    wr = f"{(stats['win']/total*100):.1f}%" if total > 0 else "N/A"
    return f"Bot Running! | Signals: {stats['signals']} | Win Rate: {wr} ({stats['win']}W/{stats['loss']}L)"

threading.Thread(target=run_scanner, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)