from flask import Flask
import requests, threading, time, ccxt, pandas as pd, math, numpy as np

app = Flask(__name__)

BOT_TOKEN = "8218941018:AAEMUIKxhYjHBtdsTp_1cSQoKoN67g6pNvI"
CHAT_ID = "1603606771"
PAIRS = ["BTC/USDT:USDT","ETH/USDT:USDT","SOL/USDT:USDT","BNB/USDT:USDT","XRP/USDT:USDT","SUI/USDT:USDT","AVA/USDT:USDT","DOGE/USDT:USDT","HYPE/USDT:USDT","BCH/USDT:USDT","ASTER/USDT:USDT"]

# Settings
SS_SMOOTH = 5
SS_FAST   = 50
SS_SLOW   = 100
VWAP_LEN  = 27

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

def supersmoother(src, length):
    a1 = math.exp(-1.414 * math.pi / length)
    b1 = 2 * a1 * math.cos(math.radians(1.414 * 180 / length))
    c2 = b1
    c3 = -(a1 ** 2)
    c1 = 1 - c2 - c3
    ss = np.zeros(len(src))
    src_arr = src.values
    for i in range(2, len(src_arr)):
        ss[i] = c1 * (src_arr[i] + src_arr[i-1]) / 2 + c2 * ss[i-1] + c3 * ss[i-2]
    return pd.Series(ss, index=src.index)

def vwap_channel(high, low, close, volume, length):
    tp = (high + low + close) / 3
    vwap = (tp * volume).rolling(length).sum() / volume.rolling(length).sum()
    std = tp.rolling(length).std()
    return vwap, vwap + std, vwap - std

def get_signal(df):
    """Returns 'LONG', 'SHORT', or None for a given OHLCV dataframe"""
    close = df["c"]
    high  = df["h"]
    low   = df["l"]
    vol   = df["v"]

    smooth_price = supersmoother(close, SS_SMOOTH)
    fast_ss      = supersmoother(smooth_price, SS_FAST)
    slow_ss      = supersmoother(smooth_price, SS_SLOW)
    osc          = fast_ss - slow_ss

    vwap, vwap_upper, vwap_lower = vwap_channel(high, low, close, vol, VWAP_LEN)

    osc_curr  = osc.iloc[-1]
    osc_prev  = osc.iloc[-2]
    price     = close.iloc[-1]
    vwap_up   = vwap_upper.iloc[-1]
    vwap_lo   = vwap_lower.iloc[-1]

    if osc_curr > 0 and osc_prev <= 0 and price > vwap_lo:
        return "LONG", float(osc_curr), float(vwap.iloc[-1])
    elif osc_curr < 0 and osc_prev >= 0 and price < vwap_up:
        return "SHORT", float(osc_curr), float(vwap.iloc[-1])
    return None, float(osc_curr), float(vwap.iloc[-1])

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
        "\U0001f680 <b>Bot v2 Started!</b>\n"
        "Pairs: " + str(len(PAIRS)) + "\n"
        "Strategy: Supersmoother Osc + VWAP Channel\n"
        "TF: 1m + 15m (dual confirmation)\n"
        "TP1: +3% | TP2: +5% | SL: -1%"
    )
    while True:
        for pair in PAIRS:
            if pair in active_signals:
                continue
            try:
                # Fetch both TFs
                ohlcv_1m  = exchange_global.fetch_ohlcv(pair, "1m",  limit=300)
                time.sleep(1)
                ohlcv_15m = exchange_global.fetch_ohlcv(pair, "15m", limit=300)

                df_1m  = pd.DataFrame(ohlcv_1m,  columns=["t","o","h","l","c","v"])
                df_15m = pd.DataFrame(ohlcv_15m, columns=["t","o","h","l","c","v"])

                sig_1m,  osc_1m,  vwap_1m  = get_signal(df_1m)
                sig_15m, osc_15m, vwap_15m = get_signal(df_15m)

                # Only signal if BOTH TFs agree
                if sig_1m is None or sig_15m is None or sig_1m != sig_15m:
                    alerted[pair] = {}
                    continue

                action = sig_1m
                price  = df_1m["c"].iloc[-1]
                now    = time.time()
                last   = alerted.get(pair, {})

                dir_key = "LONG" if action == "LONG" else "SHORT"
                if last.get("dir") == dir_key and now - last.get("t", 0) < 14400:
                    continue

                alerted[pair] = {"dir": dir_key, "t": now}
                tp1, tp2, sl = calc_tp_sl(price, action)
                active_signals[pair] = action

                if action == "LONG":
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
                        "\U0001f50d <b>Konfirmasi:</b>\n"
                        "\u2022 1m Osc: " + str(round(osc_1m, 4)) + " \u2191 \u2705\n"
                        "\u2022 15m Osc: " + str(round(osc_15m, 4)) + " \u2191 \u2705\n"
                        "\u2022 VWAP 1m: $" + fmt(vwap_1m) + "\n"
                        "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
                        "\U0001f4ca Win Rate: " + get_winrate() + "\n"
                        "\u23f0 TF: 1m+15m | Binance Futures"
                    )
                else:
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
                        "\U0001f50d <b>Konfirmasi:</b>\n"
                        "\u2022 1m Osc: " + str(round(osc_1m, 4)) + " \u2193 \u2705\n"
                        "\u2022 15m Osc: " + str(round(osc_15m, 4)) + " \u2193 \u2705\n"
                        "\u2022 VWAP 1m: $" + fmt(vwap_1m) + "\n"
                        "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
                        "\U0001f4ca Win Rate: " + get_winrate() + "\n"
                        "\u23f0 TF: 1m+15m | Binance Futures"
                    )

                send_tele(msg)
                threading.Thread(target=monitor_signal, args=(pair, action, price, tp1, tp2, sl), daemon=True).start()

            except Exception as e:
                print("scan err:", pair, e)
            time.sleep(3)
        time.sleep(300)

threading.Thread(target=scan, daemon=True).start()

@app.route("/")
def home():
    total = stats["win"] + stats["loss"]
    return "Bot Running! | Signals: " + str(total) + " | Win Rate: " + get_winrate(), 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)