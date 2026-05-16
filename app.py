import yfinance as yf
import pandas as pd
import requests
from bs4 import BeautifulSoup
from flask import Flask, render_template, jsonify
from datetime import datetime
import os

app = Flask(__name__)

# 使用 Session 偽裝並增加 Retry 機制
session = requests.Session()
session.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
})

def get_chinese_name(ticker):
    try:
        url = f"https://tw.stock.yahoo.com/quote/{ticker}"
        r = session.get(url, timeout=10)
        soup = BeautifulSoup(r.text, 'html.parser')
        name_tag = soup.find('h1', class_='C($c-link-text)')
        return name_tag.text.strip() if name_tag else ticker
    except: return ticker

@app.route('/')
def index(): return render_template('index.html')

@app.route('/api/market')
def market_api():
    try:
        # 使用專用 Session 下載匯率
        ntd = yf.download("TWD=X", period="5d", interval="1d", session=session, progress=False)
        curr_fx = round(ntd['Close'].iloc[-1], 2)
        return jsonify({"status": "success", "fx": curr_fx, "spikes": []})
    except:
        return jsonify({"status": "success", "fx": 32.1})

@app.route('/api/stock/<ticker>')
def stock_api(ticker):
    try:
        df_1m = pd.DataFrame()
        df_1d = pd.DataFrame()
        
        for suffix in [".TW", ".TWO"]:
            target = f"{ticker}{suffix}"
            # 雲端版關鍵：加入 session 與提高 timeout
            df_1m = yf.download(target, period="5d", interval="1m", session=session, progress=False, timeout=20)
            if not df_1m.empty:
                df_1d = yf.download(target, period="3mo", interval="1d", session=session, progress=False, timeout=20)
                break
        
        if df_1m.empty: return jsonify({"status": "error"})

        last_day = df_1m.index[-1].date()
        df_today = df_1m[df_1m.index.date == last_day]
        ma20 = df_1d['Close'].rolling(window=20).mean().iloc[-1]
        curr_p = df_today['Close'].iloc[-1]
        ma5 = df_today['Close'].tail(5).mean()
        
        action = "ENTRY" if curr_p > ma5 * 1.002 else "EXIT" if curr_p < ma5 * 0.998 else "HOLD"

        return jsonify({
            "status": "success", "name": get_chinese_name(ticker), "price": round(curr_p, 2), 
            "ma20": round(ma20, 2), "is_above_ma20": bool(curr_p > ma20), "action": action, 
            "date": last_day.strftime('%Y-%m-%d'),
            "intraday": {"labels": df_today.index.strftime('%H:%M').tolist(), "prices": df_today['Close'].round(2).tolist()}
        })
    except: return jsonify({"status": "error"})

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)