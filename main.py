from flask import Flask
import requests, threading, time, ccxt, pandas as pd, math, numpy as np

app = Flask(__name__)

BOT_TOKEN = "8218941018:AAEMUIKxhYjHBtdsTp_1cSQoKoN67g6pNvI"
CHAT_ID = "1603606771"
PAIRS = ["BTC/USDT:USDT","ETH/USDT:USDT","SOL/USDT:USDT","BNB/USDT:USDT","XRP/USDT:USDT","SUI/USDT:USDT","DOGE/USDT:USDT","HYPE/USDT:USDT","BCH/USDT:USDT","ADA/USDT:USDT","LINK/USDT:USDT"]

# ── Supertrend Parameters ────────────────────────────
# 3 Supertrend layers with different multiplier/period combos
# Optimized from Freqtrade hyperopt results
ST_BUY = [
    {"mult": 4, "period": 8},   # ST1 — fast
    {"mult": 7, "period": 9},   # ST2 — medium
    {"mult": 1, "period": 8},   # ST3 — sensitive
]
ST_SELL = [
    {"mult": 1, "period": 16},  # ST1 — tight
    {"mult": 3, "period": 18},  # ST2 — medium
    {"mult": 6, "period": 18},  # ST3 — wide
]

# Trailing stop settings (from Freqtrade Supertrend strategy)
TRAILING_STOP_PCT = 0.05          # 5% trailing
TRAILING_ACTIVATE_PCT = 0.015     # activate after 1.5% profit

# TP/SL
TP1_PCT = 0.03   # +3%
TP2_PCT = 0.05   # +5%
SL_PCT  = 0.012  # -1.2% (tighter with Supertrend confirmation)

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
    try:
        requests.post(
            "https://api.telegram.org/bot" + BOT_TOKEN + "/sendMessage",
            json={"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception as e:
        print("Telegram error:", e)

def get_winrate():
    total = stats["win"] + stats["loss"]
    if total == 0:
        return "N/A (baru mulai)"
    pct = round(stats["win"] / total * 100, 1)
    return str(pct) + "% (" + str(stats["win"]) + "W/" + str(stats["loss"]) + "L)"

def calc_tp_sl(price, action):
    if action == "LONG":
        return price * (1 + TP1_PCT), price * (1 + TP2_PCT), price * (1 - SL_PCT)
    else:
        return price * (1 - TP1_PCT), price * (1 - TP2_PCT), price * (1 + SL_PCT)

# ── Supertrend Indicator ─────────────────────────────

def calc_atr(df, period):
    """ATR calculation."""
    high = df["h"]
    low = df["l"]
    close = df["c"]
    tr1 = high - low
    tr2 = abs(high - close.shift(1))
    tr3 = abs(low - close.shift(1))
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(window=period).mean()

def supertrend(df, multiplier, period):
    """
    Supertrend indicator.
    Returns Series of 'up' or 'down' for each candle.
    """
    hl2 = (df["h"] + df["l"]) / 2
    atr = calc_atr(df, period)

    upper_band = hl2 + (multiplier * atr)
    lower_band = hl2 - (multiplier * atr)

    st_direction = pd.Series(index=df.index, dtype=object)
    st_direction.iloc[0] = "up"

    final_upper = upper_band.copy()
    final_lower = lower_band.copy()

    for i in range(1, len(df)):
        # Lower band logic
        if lower_band.iloc[i] > final_lower.iloc[i-1] or df["c"].iloc[i-1] < final_lower.iloc[i-1]:
            final_lower.iloc[i] = lower_band.iloc[i]
        else:
            final_lower.iloc[i] = final_lower.iloc[i-1]

        # Upper band logic
        if upper_band.iloc[i] < final_upper.iloc[i-1] or df["c"].iloc[i-1] > final_upper.iloc[i-1]:
            final_upper.iloc[i] = upper_band.iloc[i]
        else:
            final_upper.iloc[i] = final_upper.iloc[i-1]

        # Direction
        if st_direction.iloc[i-1] == "up":
            if df["c"].iloc[i] < final_lower.iloc[i]:
                st_direction.iloc[i] = "down"
            else:
                st_direction.iloc[i] = "up"
        else:
            if df["c"].iloc[i] > final_upper.iloc[i]:
                st_direction.iloc[i] = "up"
            else:
                st_direction.iloc[i] = "down"

    return st_direction

# ── Signal Logic ─────────────────────────────────────

def get_signal(df, st_params):
    """
    3x Supertrend must ALL agree for signal.
    Returns 'LONG', 'SHORT', or None.
    """
    results = []
    for p in st_params:
        st = supertrend(df, p["mult"], p["period"])
        results.append(st.iloc[-1])

    # All 3 must be 'up' for LONG
    if all(r == "up" for r in results):
        return "LONG", results
    # All 3 must be 'down' for SHORT
    elif all(r == "down" for r in results):
        return "SHORT", results
    return None, results

def check_signal(df):
    """
    Check both buy and sell supertrend sets.
    Buy ST all 'up' = LONG signal
    Sell ST all 'down' = SHORT signal
    """
    buy_sig, buy_details = get_signal(df, ST_BUY)
    sell_sig, sell_details = get_signal(df, ST_SELL)

    if buy_sig == "LONG":
        return "LONG", buy_details, sell_details
    elif sell_sig == "SHORT":
        return "SHORT", buy_details, sell_details
    return None, buy_details, sell_details


def monitor_signal(pair, action, entry, tp1, tp2, sl):
    """Monitor with trailing stop."""
    tp1_hit = False
    best_price = entry
    trailing_active = False
    trailing_sl = sl
    deadline = time.time() + 14400  # 4 hours max

    while time.time() < deadline:
        try:
            time.sleep(45)  # check every 45s
            price = exchange_global.fetch_ticker(pair)["last"]

            # Update best price and trailing stop
            if action == "LONG":
                if price > best_price:
                    best_price = price
                profit_pct = (best_price - entry) / entry

                # Activate trailing after threshold
                if profit_pct >= TRAILING_ACTIVATE_PCT:
                    trailing_active = True
                    new_trail_sl = best_price * (1 - TRAILING_STOP_PCT)
                    trailing_sl = max(trailing_sl, new_trail_sl, sl)

                effective_sl = trailing_sl if trailing_active else sl

                if not tp1_hit and price >= tp1:
                    tp1_hit = True
                    send_tele("🎯 <b>TP1 HIT!</b>\nPair: " + pair + "\nSignal: LONG\nTP1: $" + fmt(tp1) + "\n\nHolding for TP2: $" + fmt(tp2) + "\nTrailing SL: $" + fmt(trailing_sl))
                elif tp1_hit and price >= tp2:
                    stats["win"] += 1
                    send_tele("💰 <b>TP2 HIT! BIG WIN!</b>\nPair: " + pair + "\nSignal: LONG\nTP2: $" + fmt(tp2) + "\n\n📊 Win Rate: " + get_winrate())
                    active_signals.pop(pair, None); return
                elif price <= effective_sl:
                    if tp1_hit or trailing_active:
                        stats["win"] += 1
                        pnl_pct = round((price - entry) / entry * 100, 2)
                        send_tele("🔒 <b>Trailing SL — Profit Locked!</b>\nPair: " + pair + "\nP&L: " + str(pnl_pct) + "%\n\n📊 Win Rate: " + get_winrate())
                    else:
                        stats["loss"] += 1
                        send_tele("❌ <b>SL HIT!</b>\nPair: " + pair + "\nSignal: LONG\nSL: $" + fmt(effective_sl) + "\n\n📊 Win Rate: " + get_winrate())
                    active_signals.pop(pair, None); return

            else:  # SHORT
                if price < best_price:
                    best_price = price
                profit_pct = (entry - best_price) / entry

                if profit_pct >= TRAILING_ACTIVATE_PCT:
                    trailing_active = True
                    new_trail_sl = best_price * (1 + TRAILING_STOP_PCT)
                    trailing_sl = min(trailing_sl, new_trail_sl) if trailing_active else new_trail_sl
                    trailing_sl = min(trailing_sl, sl)

                effective_sl = trailing_sl if trailing_active else sl

                if not tp1_hit and price <= tp1:
                    tp1_hit = True
                    send_tele("🎯 <b>TP1 HIT!</b>\nPair: " + pair + "\nSignal: SHORT\nTP1: $" + fmt(tp1) + "\n\nHolding for TP2: $" + fmt(tp2) + "\nTrailing SL: $" + fmt(trailing_sl))
                elif tp1_hit and price <= tp2:
                    stats["win"] += 1
                    send_tele("💰 <b>TP2 HIT! BIG WIN!</b>\nPair: " + pair + "\nSignal: SHORT\nTP2: $" + fmt(tp2) + "\n\n📊 Win Rate: " + get_winrate())
                    active_signals.pop(pair, None); return
                elif price >= effective_sl:
                    if tp1_hit or trailing_active:
                        stats["win"] += 1
                        pnl_pct = round((entry - price) / entry * 100, 2)
                        send_tele("🔒 <b>Trailing SL — Profit Locked!</b>\nPair: " + pair + "\nP&L: " + str(pnl_pct) + "%\n\n📊 Win Rate: " + get_winrate())
                    else:
                        stats["loss"] += 1
                        send_tele("❌ <b>SL HIT!</b>\nPair: " + pair + "\nSignal: SHORT\nSL: $" + fmt(effective_sl) + "\n\n📊 Win Rate: " + get_winrate())
                    active_signals.pop(pair, None); return

        except Exception as e:
            print("monitor err:", e)
            time.sleep(60)

    # Deadline reached
    active_signals.pop(pair, None)


def scan():
    global exchange_global
    exchange_global = ccxt.bybit({"enableRateLimit": True, "options": {"defaultType": "swap"}})
    send_tele(
        "🚀 <b>Bot v2 Started — SUPERTREND!</b>\n"
        "━━━━━━━━━━━━━━\n"
        "Pairs: " + str(len(PAIRS)) + "\n"
        "Strategy: 3x Supertrend + Trailing Stop\n"
        "TF: 15m (primary) + 1H (confirm)\n"
        "TP1: +3% | TP2: +5% | SL: -1.2%\n"
        "Trailing: 5% after 1.5% profit\n"
        "Exchange: Bybit"
    )
    while True:
        for pair in PAIRS:
            if pair in active_signals:
                continue
            try:
                # Primary: 15m timeframe
                ohlcv_15m = exchange_global.fetch_ohlcv(pair, "15m", limit=200)
                time.sleep(1)
                # Confirm: 1H timeframe
                ohlcv_1h = exchange_global.fetch_ohlcv(pair, "1h", limit=200)

                df_15m = pd.DataFrame(ohlcv_15m, columns=["t","o","h","l","c","v"])
                df_1h  = pd.DataFrame(ohlcv_1h,  columns=["t","o","h","l","c","v"])

                # Get signals from both TFs
                sig_15m, buy_det_15m, sell_det_15m = check_signal(df_15m)
                sig_1h,  buy_det_1h,  sell_det_1h  = check_signal(df_1h)

                # Need 15m signal + 1H confirmation (same direction)
                if sig_15m is None:
                    continue

                # 1H must agree OR at least 2/3 supertrends agree on 1H
                if sig_1h != sig_15m:
                    # Check if at least 2/3 of buy/sell ST agree on 1H
                    if sig_15m == "LONG":
                        up_count = sum(1 for r in buy_det_1h if r == "up")
                        if up_count < 2:
                            continue
                    else:
                        down_count = sum(1 for r in sell_det_1h if r == "down")
                        if down_count < 2:
                            continue

                action = sig_15m
                price  = df_15m["c"].iloc[-1]
                now    = time.time()
                last   = alerted.get(pair, {})

                dir_key = action
                if last.get("dir") == dir_key and now - last.get("t", 0) < 14400:
                    continue

                alerted[pair] = {"dir": dir_key, "t": now}
                tp1, tp2, sl = calc_tp_sl(price, action)
                active_signals[pair] = action

                # Build confirmation text
                if action == "LONG":
                    st_txt = "".join(["🟢" if r == "up" else "🔴" for r in buy_det_15m])
                    st_1h  = "".join(["🟢" if r == "up" else "🔴" for r in buy_det_1h])
                    arrow = "🟢"
                    direction = "LONG"
                else:
                    st_txt = "".join(["🔴" if r == "down" else "🟢" for r in sell_det_15m])
                    st_1h  = "".join(["🔴" if r == "down" else "🟢" for r in sell_det_1h])
                    arrow = "🔴"
                    direction = "SHORT"

                msg = (
                    "🚨 <b>SUPERTREND SIGNAL!</b>\n"
                    "━━━━━━━━━━━━━━\n"
                    "📌 Pair: <b>" + pair + "</b>\n"
                    "📊 Signal: " + arrow + " <b>" + direction + "</b>\n"
                    "━━━━━━━━━━━━━━\n"
                    "💵 Entry: <b>$" + fmt(price) + "</b>\n"
                    "🎯 TP1: $" + fmt(tp1) + " (+" + str(TP1_PCT*100) + "%)\n"
                    "🎯 TP2: $" + fmt(tp2) + " (+" + str(TP2_PCT*100) + "%)\n"
                    "🛑 SL: $" + fmt(sl) + " (-" + str(SL_PCT*100) + "%)\n"
                    "━━━━━━━━━━━━━━\n"
                    "🔍 <b>Supertrend:</b>\n"
                    "• 15m: " + st_txt + " ✅\n"
                    "• 1H:  " + st_1h + "\n"
                    "• Trailing: 5% after +1.5%\n"
                    "━━━━━━━━━━━━━━\n"
                    "📊 Win Rate: " + get_winrate() + "\n"
                    "⏰ Bybit Futures | Supertrend v2"
                )

                send_tele(msg)
                threading.Thread(target=monitor_signal, args=(pair, action, price, tp1, tp2, sl), daemon=True).start()

            except Exception as e:
                print("scan err:", pair, e)
            time.sleep(3)
        time.sleep(120)  # scan every 2 min (15m TF doesn't need 5min wait)


threading.Thread(target=scan, daemon=True).start()

@app.route("/")
def home():
    total = stats["win"] + stats["loss"]
    active = ", ".join([p + "(" + a + ")" for p, a in active_signals.items()]) or "none"
    return "Supertrend Bot Running! | Signals: " + str(total) + " | Win Rate: " + get_winrate() + " | Active: " + active, 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
