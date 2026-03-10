from flask import Flask, request
import requests, threading, time, ccxt, pandas as pd, math
from ta.trend import EMAIndicator
from ta.momentum import RSIIndicator, StochasticOscillator

app = Flask(__name__)

BOT_TOKEN = "8218941018:AAEMUIKxhYjHBtdsTp_1cSQoKoN67g6pNvI"
CHAT_ID = "1603606771"
PAIRS = ["BTC/USDT:USDT","ETH/USDT:USDT","SOL/USDT:USDT","BNB/USDT:USDT","XRP/USDT:USDT","SUI/USDT:USDT","AVA/USDT:USDT","DOGE/USDT:USDT","HYPE/USDT:USDT","BCH/USDT:USDT","ASTER/USDT:USDT"]
alerted = {}
stats = {"win": 0, "loss": 0}
active_signals = {}
exchange_global = None

def fmt(price):
    if price == 0:
        return "0"
    d = math.floor(math.log10(abs(price)))
    decimals = max(2, 4 - d)
    return str(round(price, decimals))

def send_tele(msg):
    requests.post(
        "https://api.telegram.org/bot" + BOT_TOKEN + "/sendMessage",
        json={"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"}
    )

def get_winrate():
    total = stats["win"] + stats["loss"]
    if total == 0:
        return "N/A (baru mulai)"
    pct = round(stats["win"] / total * 100, 1)
    return str(pct) + "% (" + str(stats["win"]) + "W/" + str(stats["loss"]) + "L)"

def calc_tp_sl(price, action):
    if action == "LONG":
        return price * 1.03, price * 1.05, price * 0.99
    else:
        return price * 0.97, price * 0.95, price * 1.015

def monitor_signal(pair, action, entry, tp1, tp2, sl):
    tp1_hit = False
    deadline = time.time() + 14400
    while time.time() < deadline:
        try:
            time.sleep(60)
            price = exchange_global.fetch_ticker(pair)["last"]
            if action == "LONG":
                if not tp1_hit and price >= tp1:
                    tp1_hit = True
                    send_tele("\U0001f3af <b>TP1 HIT!</b>\nPair: " + pair + "\nSignal: LONG\nTP1: $" + fmt(tp1) + "\n\nHolding for TP2: $" + fmt(tp2))
                elif tp1_hit and price >= tp2:
                    stats["win"] += 1
                    send_tele("\U0001f4b0 <b>TP2 HIT! WIN!</b>\nPair: " + pair + "\nSignal: LONG\nTP2: $" + fmt(tp2) + "\n\n\U0001f4ca Win Rate: " + get_winrate())
                    active_signals.pop(pair, None); return
                elif price <= sl:
                    if tp1_hit:
                        send_tele("\u26a0\ufe0f <b>SL HIT after TP1</b>\nPair: " + pair + "\nPartial win\n\n\U0001f4ca Win Rate: " + get_winrate())
                    else:
                        stats["loss"] += 1
                        send_tele("\u274c <b>SL HIT!</b>\nPair: " + pair + "\nSignal: LONG\nSL: $" + fmt(sl) + "\n\n\U0001f4ca Win Rate: " + get_winrate())
                    active_signals.pop(pair, None); return
            else:
                if not tp1_hit and price <= tp1:
                    tp1_hit = True
                    send_tele("\U0001f3af <b>TP1 HIT!</b>\nPair: " + pair + "\nSignal: SHORT\nTP1: $" + fmt(tp1) + "\n\nHolding for TP2: $" + fmt(tp2))
                elif tp1_hit and price <= tp2:
                    stats["win"] += 1
                    send_tele("\U0001f4b0 <b>TP2 HIT! WIN!</b>\nPair: " + pair + "\nSignal: SHORT\nTP2: $" + fmt(tp2) + "\n\n\U0001f4ca Win Rate: " + get_winrate())
                    active_signals.pop(pair, None); return
                elif price >= sl:
                    if tp1_hit:
                        send_tele("\u26a0\ufe0f <b>SL HIT after TP1</b>\nPair: " + pair + "\nPartial win\n\n\U0001f4ca Win Rate: " + get_winrate())
                    else:
                        stats["loss"] += 1
                        send_tele("\u274c <b>SL HIT!</b>\nPair: " + pair + "\nSignal: SHORT\nSL: $" + fmt(sl) + "\n\n\U0001f4ca Win Rate: " + get_winrate())
                    active_signals.pop(pair, None); return
        except Exception as e:
            print("monitor err:", e)

def scan():
    global exchange_global
    exchange_global = ccxt.binanceusdm({"enableRateLimit": True})
    send_tele(
        "\U0001f680 <b>Bot 1H Started!</b>\n"
        "Pairs: " + str(len(PAIRS)) + "\n"
        "Strategy: Single TF 1H\n"
        "TP1: +3% | TP2: +5% | SL: -1%"
    )
    while True:
        for pair in PAIRS:
            if pair in active_signals:
                continue
            try:
                ohlcv = exchange_global.fetch_ohlcv(pair, "1h", limit=150)
                df = pd.DataFrame(ohlcv, columns=["t","o","h","l","c","v"])
                close = df["c"]
                high = df["h"]
                low = df["l"]
                ema25 = EMAIndicator(close, 25).ema_indicator()
                ema75 = EMAIndicator(close, 75).ema_indicator()
                ema140 = EMAIndicator(close, 140).ema_indicator()
                rsi = RSIIndicator(close, 14).rsi()
                stoch = StochasticOscillator(high, low, close, 14, 3)
                stoch_k = stoch.stoch()
                stoch_d = stoch.stoch_signal()
                i = -1
                p = -2
                price = close.iloc[i]
                rsi_val = round(rsi.iloc[i], 1)
                stoch_val = round(stoch_k.iloc[i], 1)
                now = time.time()
                last = alerted.get(pair, {})

                long_signal = (
                    ema25.iloc[i] > ema75.iloc[i] > ema140.iloc[i] and
                    close.iloc[i] > ema25.iloc[i] and
                    stoch_k.iloc[i] > stoch_d.iloc[i] and
                    stoch_k.iloc[p] <= stoch_d.iloc[p] and
                    stoch_k.iloc[i] < 80 and
                    rsi.iloc[i] > 40
                )
                short_signal = (
                    ema25.iloc[i] < ema75.iloc[i] < ema140.iloc[i] and
                    close.iloc[i] < ema25.iloc[i] and
                    stoch_k.iloc[i] < stoch_d.iloc[i] and
                    stoch_k.iloc[p] >= stoch_d.iloc[p] and
                    stoch_k.iloc[i] > 20 and
                    rsi.iloc[i] < 60
                )

                if long_signal:
                    if last.get("dir") != "LONG" or now - last.get("t", 0) > 14400:
                        alerted[pair] = {"dir": "LONG", "t": now}
                        tp1, tp2, sl = calc_tp_sl(price, "LONG")
                        active_signals[pair] = "LONG"
                        msg = (
                            "\U0001f6a8 <b>SIGNAL ALERT!</b>\n"
                            "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
                            "\U0001f4cc Pair: <b>" + pair + "</b>\n"
                            "\U0001f4ca Signal: \U0001f7e2 <b>LONG</b>\n"
                            "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
                            "\U0001f4c8 Entry: <b>$" + fmt(price) + "</b>\n"
                            "\U0001f3af TP1: $" + fmt(tp1) + " (+3%)\n"
                            "\U0001f3af TP2: $" + fmt(tp2) + " (+5%)\n"
                            "\U0001f6d1 SL: $" + fmt(sl) + " (-1%)\n"
                            "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
                            "\U0001f50d <b>Analisis:</b>\n"
                            "\u2022 EMA25 > EMA75 > EMA140 \u2192 Uptrend \u2705\n"
                            "\u2022 Stochastic oversold crossup \u2705\n"
                            "\u2022 RSI: " + str(rsi_val) + " | Stoch: " + str(stoch_val) + " \u2705\n"
                            "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
                            "\U0001f4ca Win Rate: " + get_winrate() + "\n"
                            "\u23f0 TF: 1H | Binance Futures"
                        )
                        send_tele(msg)
                        threading.Thread(target=monitor_signal, args=(pair, "LONG", price, tp1, tp2, sl), daemon=True).start()
                elif short_signal:
                    if last.get("dir") != "SHORT" or now - last.get("t", 0) > 14400:
                        alerted[pair] = {"dir": "SHORT", "t": now}
                        tp1, tp2, sl = calc_tp_sl(price, "SHORT")
                        active_signals[pair] = "SHORT"
                        msg = (
                            "\U0001f6a8 <b>SIGNAL ALERT!</b>\n"
                            "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
                            "\U0001f4cc Pair: <b>" + pair + "</b>\n"
                            "\U0001f4ca Signal: \U0001f534 <b>SHORT</b>\n"
                            "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
                            "\U0001f4c9 Entry: <b>$" + fmt(price) + "</b>\n"
                            "\U0001f3af TP1: $" + fmt(tp1) + " (-3%)\n"
                            "\U0001f3af TP2: $" + fmt(tp2) + " (-5%)\n"
                            "\U0001f6d1 SL: $" + fmt(sl) + " (+1.5%)\n"
                            "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
                            "\U0001f50d <b>Analisis:</b>\n"
                            "\u2022 EMA25 < EMA75 < EMA140 \u2192 Downtrend \u2705\n"
                            "\u2022 Stochastic overbought crossdown \u2705\n"
                            "\u2022 RSI: " + str(rsi_val) + " | Stoch: " + str(stoch_val) + " \u2705\n"
                            "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
                            "\U0001f4ca Win Rate: " + get_winrate() + "\n"
                            "\u23f0 TF: 1H | Binance Futures"
                        )
                        send_tele(msg)
                        threading.Thread(target=monitor_signal, args=(pair, "SHORT", price, tp1, tp2, sl), daemon=True).start()
                else:
                    alerted[pair] = {}
            except Exception as e:
                print("scan err:", pair, e)
        time.sleep(300)

threading.Thread(target=scan, daemon=True).start()

@app.route("/")
def home():
    total = stats["win"] + stats["loss"]
    return "Bot Running! | Signals: " + str(total) + " | Win Rate: " + get_winrate(), 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
