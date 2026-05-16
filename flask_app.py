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

# 背景監控名單 (上市櫃熱門)
MONITOR_POOL = [
    "2330.TW","2317.TW","2454.TW","2303.TW","2382.TW","3231.TW","3481.TW","2409.TW",
    "5314.TWO","8069.TWO","5483.TWO","3293.TWO","1513.TW","1519.TW","2603.TW"
]

cache = {"spikes": [], "last_scan": "--:--:--", "names": {}, "fx": 32.1}

def get_stock_name(ticker):
    pure = ticker.split('.')[0]
    if pure in cache["names"]: return cache["names"][pure]
    try:
        url = f"https://tw.stock.yahoo.com/quote/{pure}"
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        r = requests.get(url, headers=headers, timeout=5)
        soup = BeautifulSoup(r.text, 'html.parser')
        name_tag = soup.find('h1', class_='C($c-link-text)')
        name = name_tag.text.strip() if name_tag else pure
        cache["names"][pure] = name
        return name
    except: return pure

def background_scanner():
    while True:
        try:
            # 使用 download 提升雲端穩定性
            data = yf.download(MONITOR_POOL, period="2d", group_by='ticker', threads=True, progress=False, timeout=20)
            temp_spikes = []
            for t in MONITOR_POOL:
                try:
                    s_df = data[t]
                    if len(s_df) < 2: continue
                    vol_t = s_df['Volume'].iloc[-1]
                    vol_y = s_df['Volume'].iloc[-2]
                    if vol_t > vol_y * 1.8 and vol_t > 500:
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
        fx_data = yf.download("TWD=X", period="1d", progress=False, timeout=10)
        if not fx_data.empty: cache["fx"] = round(fx_data['Close'].iloc[-1], 2)
        return jsonify({
            "status": "success", "fx": cache["fx"],
            "spikes": cache["spikes"], "last_scan": cache["last_scan"]
        })
    except:
        return jsonify({"status": "success", "fx": cache["fx"], "spikes": cache["spikes"], "last_scan": "Lag"})

@app.route('/api/stock/<ticker>')
def stock_api(ticker):
    try:
        df_1m = pd.DataFrame()
        df_1d = pd.DataFrame()
        # 雲端必須分開嘗試上市與上櫃
        for suffix in [".TW", ".TWO"]:
            target = ticker + suffix
            df_1m = yf.download(target, period="5d", interval="1m", progress=False, timeout=15)
            if not df_1m.empty:
                df_1d = yf.download(target, period="3mo", interval="1d", progress=False, timeout=15)
                break
        
        if df_1m.empty: return jsonify({"status": "error"})

        last_day = df_1m.index[-1].date()
        df_today = df_1m[df_1m.index.date == last_day]
        ma20 = df_1d['Close'].rolling(window=20).mean().iloc[-1]
        curr_p = df_today['Close'].iloc[-1]
        ma5 = df_today['Close'].tail(5).mean()
        
        action = "HOLD"
        upper_shadow = df_today['High'].iloc[-1] - max(df_today['Open'].iloc[-1], curr_p)
        if curr_p < ma5 * 0.998 or upper_shadow > (df_today['High'].iloc[-1]-df_today['Low'].iloc[-1])*0.5: action = "EXIT"
        elif curr_p > ma5 * 1.002: action = "ENTRY"

        return jsonify({
            "status": "success", "name": get_stock_name(ticker), "price": round(curr_p, 2), 
            "ma20": round(ma20, 2), "is_above_ma20": bool(curr_p > ma20), "action": action, 
            "date": last_day.strftime('%Y-%m-%d'),
            "intraday": {"labels": df_today.index.strftime('%H:%M').tolist(), "prices": df_today['Close'].round(2).tolist()}
        })
    except: return jsonify({"status": "error"})

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)