import yfinance as yf
import pandas as pd
import requests
from bs4 import BeautifulSoup
from flask import Flask, render_template, jsonify

app = Flask(__name__)


def scalar(value):
    if isinstance(value, pd.Series):
        return value.iloc[0]
    if hasattr(value, "item"):
        return value.item()
    return value


def column(df, name):
    values = df[name]
    if isinstance(values, pd.DataFrame):
        return values.iloc[:, 0]
    return values


def get_chinese_name(ticker):
    try:
        url = f"https://tw.stock.yahoo.com/quote/{ticker}"
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(url, headers=headers, timeout=5)
        soup = BeautifulSoup(r.text, "html.parser")
        name_tag = soup.find("h1", class_="C($c-link-text)")
        return name_tag.text.strip() if name_tag else ticker
    except Exception:
        return ticker


def calc_rsi(close, period=14):
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(window=period).mean()
    avg_loss = loss.rolling(window=period).mean()
    rs = avg_gain / avg_loss.replace(0, pd.NA)
    rsi = 100 - (100 / (1 + rs))
    return rsi.iloc[-1]


def build_signal(curr_p, ma5, ma20, rsi, volume_ratio):
    score = 0
    reasons = []
    warnings = []

    if curr_p > ma20:
        score += 2
        reasons.append("站上日線 20MA，波段結構偏多")
    else:
        score -= 2
        warnings.append("跌破日線 20MA，波段結構偏弱")

    if curr_p > ma5 * 1.002:
        score += 1
        reasons.append("現價突破短線 MA5")
    elif curr_p < ma5 * 0.998:
        score -= 1
        warnings.append("現價跌破短線 MA5")

    if volume_ratio >= 1.5:
        score += 1
        reasons.append(f"近 5 分鐘量能放大 {volume_ratio:.1f} 倍")
    elif volume_ratio < 0.7:
        score -= 1
        warnings.append("量能不足，突破可信度較低")

    if pd.notna(rsi):
        if rsi > 75:
            score -= 1
            warnings.append(f"RSI {rsi:.1f} 偏過熱，追價風險提高")
        elif rsi < 30:
            score -= 1
            warnings.append(f"RSI {rsi:.1f} 偏弱，先觀察止跌")
        elif 45 <= rsi <= 70:
            score += 1
            reasons.append(f"RSI {rsi:.1f} 位於健康動能區")

    if score >= 4:
        action = "ENTRY"
    elif score <= -2:
        action = "EXIT"
    else:
        action = "HOLD"

    return action, score, reasons, warnings


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/market")
def market_api():
    try:
        ntd = yf.download("TWD=X", period="2d", interval="1d", progress=False)
        curr_fx = round(scalar(column(ntd, "Close").iloc[-1]), 2)
        return jsonify({"status": "success", "fx": curr_fx, "spikes": []})
    except Exception:
        return jsonify({"status": "success", "fx": 32.2, "spikes": []})


@app.route("/api/stock/<ticker>")
def stock_api(ticker):
    try:
        df_1m = pd.DataFrame()
        df_1d = pd.DataFrame()

        for suffix in [".TW", ".TWO"]:
            target = f"{ticker}{suffix}"
            df_1m = yf.download(target, period="5d", interval="1m", progress=False)
            if not df_1m.empty:
                df_1d = yf.download(target, period="3mo", interval="1d", progress=False)
                break

        if df_1m.empty or df_1d.empty:
            return jsonify({"status": "error"})

        last_day = df_1m.index[-1].date()
        df_today = df_1m[df_1m.index.date == last_day]
        if df_today.empty:
            return jsonify({"status": "error"})

        close_1d = column(df_1d, "Close")
        close_today = column(df_today, "Close")
        volume_today = column(df_today, "Volume")

        ma20 = scalar(close_1d.rolling(window=20).mean().iloc[-1])
        curr_p = scalar(close_today.iloc[-1])
        ma5 = scalar(close_today.tail(5).mean())
        rsi = scalar(calc_rsi(close_1d))

        recent_volume = scalar(volume_today.tail(5).mean())
        avg_volume = scalar(volume_today.mean())
        volume_ratio = recent_volume / avg_volume if avg_volume else 0

        action, score, reasons, warnings = build_signal(curr_p, ma5, ma20, rsi, volume_ratio)

        return jsonify({
            "status": "success",
            "name": get_chinese_name(ticker),
            "price": round(curr_p, 2),
            "ma5": round(ma5, 2),
            "ma20": round(ma20, 2),
            "rsi": round(rsi, 2) if pd.notna(rsi) else None,
            "volume_ratio": round(volume_ratio, 2),
            "score": score,
            "reasons": reasons,
            "warnings": warnings,
            "is_above_ma20": bool(curr_p > ma20),
            "action": action,
            "date": last_day.strftime("%Y-%m-%d"),
            "intraday": {
                "labels": df_today.index.strftime("%H:%M").tolist(),
                "prices": close_today.round(2).tolist(),
            },
        })
    except Exception:
        return jsonify({"status": "error"})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
