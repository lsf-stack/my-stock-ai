import yfinance as yf
import pandas as pd
import requests
from bs4 import BeautifulSoup
from flask import Flask, render_template, jsonify
from datetime import datetime
import threading
import time
import os

app = Flask(__name__)

# 背景監控清單
MONITOR_POOL = [
    "2330.TW","2317.TW","2454.TW","2303.TW","2382.TW","3231.TW","3481.TW","2409.TW",
    "5314.TWO","8069.TWO","5483.TWO","3293.TWO","1513.TW","1519.TW","2603.TW"
]

cache = {"spikes": [], "last_scan": "--:--:--", "names": {}}

def get_stock_name(ticker_full):
    pure = ticker_full.split('.')[0]
    if pure in cache["names"]: return cache["names"][pure]
    try:
        url = f"https://tw.stock.yahoo.com/quote/{pure}"
        r = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=2)
        soup = BeautifulSoup(r.text, 'html.parser')
        name_tag = soup.find('h1', class_='C($c-link-text)')
        name = name_tag.text.strip() if name_tag else pure
        cache["names"][pure] = name
        return name
    except: return pure

def background_scanner():
    while True:
        temp_spikes = []
        try:
            data = yf.download(MONITOR_POOL, period="2d", group_by='ticker', threads=True, progress=False)
            for t in MONITOR_POOL:
                try:
                    s_df = data[t]
                    if len(s_df) < 2: continue
                    vol_t = s_df['Volume'].iloc[-1]
                    vol_y = s_df['Volume'].iloc[-2]
                    if vol_t > vol_y * 2 and vol_t > 500:
                        temp_spikes.append({
                            "ticker": t.split('.')[0],
                            "name": get_stock_name(t),
                            "ratio": round(vol_t / vol_y, 1),
                            "price": round(s_df['Close'].iloc[-1], 2)
                        })
                except: continue
            cache["spikes"] = sorted(temp_spikes, key=lambda x: x['ratio'], reverse=True)
            cache["last_scan"] = datetime.now().strftime("%H:%M:%S")
        except: pass
        time.sleep(600)

threading.Thread(target=background_scanner, daemon=True).start()

@app.route('/')
def index(): return render_template('index.html')

@app.route('/api/market')
def market_api():
    try:
        fx = yf.Ticker("TWD=X").history(period="1d").iloc[-1]
        return jsonify({
            "status": "success", "fx": round(fx['Close'], 2),
            "spikes": cache["spikes"], "last_scan": cache["last_scan"]
        })
    except: return jsonify({"status": "error"})

@app.route('/api/stock/<ticker>')
def stock_api(ticker):
    try:
        df_1m = pd.DataFrame()
        # 自動識別上市或上櫃
        for suffix in [".TW", ".TWO"]:
            s = yf.Ticker(ticker + suffix)
            df_1m = s.history(period="5d", interval="1m")
            if not df_1m.empty:
                df_1d = s.history(period="3mo")
                name = get_stock_name(ticker + suffix)
                break
        
        last_day = df_1m.index[-1].date()
        df_today = df_1m[df_1m.index.date == last_day]
        ma20 = df_1d['Close'].rolling(window=20).mean().iloc[-1]
        curr_p = df_today['Close'].iloc[-1]
        ma5 = df_today['Close'].tail(5).mean()
        
        action = "HOLD"
        upper_shadow = df_today['High'].iloc[-1] - max(df_today['Open'].iloc[-1], curr_p)
        if curr_p < ma5 or upper_shadow > (df_today['High'].iloc[-1]-df_today['Low'].iloc[-1])*0.5: action = "EXIT"
        elif curr_p > ma5 * 1.002: action = "ENTRY"

        return jsonify({
            "status": "success", "name": name, "price": round(curr_p, 2), "ma20": round(ma20, 2),
            "is_above_ma20": bool(curr_p > ma20), "action": action, "date": last_day.strftime('%Y-%m-%d'),
            "intraday": {"labels": df_today.index.strftime('%H:%M').tolist(), "prices": df_today['Close'].round(2).tolist()}
        })
    except: return jsonify({"status": "error"})

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)