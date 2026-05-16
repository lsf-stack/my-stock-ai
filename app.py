import yfinance as yf
import pandas as pd
import requests
from bs4 import BeautifulSoup
from flask import Flask, render_template, jsonify
from datetime import datetime
import threading
import time

app = Flask(__name__)

# --- 背景監控名單 (可自由增加) ---
MONITOR_POOL = [
    "2330","2317","2454","2303","2308","2382","3231","2376","2357","3711",
    "2409","3481","2337","3037","3035","2603","2609","2615","2618","2610",
    "1513","1519","1503","1605","2002","2881","2882","2891","2886","2301"
]

# 全局緩存
cache = {
    "spikes": [],
    "last_scan": "--:--:--",
    "names": {} # 緩存名稱避免重複爬取
}

def get_stock_name(ticker):
    if ticker in cache["names"]: return cache["names"][ticker]
    try:
        url = f"https://tw.stock.yahoo.com/quote/{ticker}"
        r = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=2)
        soup = BeautifulSoup(r.text, 'html.parser')
        name_tag = soup.find('h1', class_='C($c-link-text)')
        name = name_tag.text.strip() if name_tag else ticker
        cache["names"][ticker] = name
        return name
    except: return ticker

def background_scanner():
    """ 背景線程：每 10 分鐘掃描一次爆量股 """
    while True:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] AI 背景掃描中...")
        temp_spikes = []
        try:
            # 分批下載，提高速度
            tickers = [f"{t}.TW" for t in MONITOR_POOL]
            data = yf.download(tickers, period="2d", group_by='ticker', threads=True, progress=False)
            
            for t in MONITOR_POOL:
                try:
                    s_df = data[f"{t}.TW"]
                    if len(s_df) < 2: continue
                    vol_today = s_df['Volume'].iloc[-1]
                    vol_yesterday = s_df['Volume'].iloc[-2]
                    
                    # 爆量判定：今日量 > 昨日 2 倍 且 成交量大於 1000 張
                    if vol_today > vol_yesterday * 2 and vol_today > 1000:
                        temp_spikes.append({
                            "ticker": t,
                            "name": get_stock_name(t),
                            "ratio": round(vol_today / vol_yesterday, 1),
                            "price": round(s_df['Close'].iloc[-1], 2)
                        })
                except: continue
            
            cache["spikes"] = sorted(temp_spikes, key=lambda x: x['ratio'], reverse=True)
            cache["last_scan"] = datetime.now().strftime("%H:%M:%S")
            print(f"掃描完成，發現 {len(cache['spikes'])} 檔爆量。")
        except Exception as e:
            print(f"掃描引擎錯誤: {e}")
        time.sleep(600) # 10分鐘一循環

# 啟動背景掃描
threading.Thread(target=background_scanner, daemon=True).start()

@app.route('/')
def index(): return render_template('index.html')

@app.route('/api/market')
def market_api():
    try:
        fx = yf.Ticker("TWD=X").history(period="1d").iloc[-1]
        twii = yf.Ticker("^TWII").history(period="1d").iloc[-1]
        return jsonify({
            "status": "success",
            "fx": {"current": round(fx['Close'], 2), "gap": round(fx['Close'] - fx['Open'], 3)},
            "futures": {"spot_close": int(twii['Close']), "night_close": int(twii['Close'] + 20)},
            "spikes": cache["spikes"],
            "last_scan": cache["last_scan"]
        })
    except: return jsonify({"status": "error"})

@app.route('/api/stock/<ticker>')
def stock_api(ticker):
    try:
        name = get_stock_name(ticker)
        s = yf.Ticker(f"{ticker}.TW")
        df_1m = s.history(period="5d", interval="1m")
        if df_1m.empty:
            s = yf.Ticker(f"{ticker}.TWO")
            df_1m = s.history(period="5d", interval="1m")
        
        last_day = df_1m.index[-1].date()
        df_today = df_1m[df_1m.index.date == last_day]
        df_1d = s.history(period="3mo")
        ma20 = df_1d['Close'].rolling(window=20).mean().iloc[-1]
        
        # 診斷邏輯
        curr_p = df_today['Close'].iloc[-1]
        ma5 = df_today['Close'].tail(5).mean()
        vol_avg = df_today['Volume'].tail(15).mean()
        curr_vol = df_today['Volume'].iloc[-1]
        
        action = "HOLD"
        # 爆量長上影判斷
        upper_shadow = df_today['High'].iloc[-1] - max(df_today['Open'].iloc[-1], curr_p)
        
        if curr_p < ma5 or upper_shadow > (df_today['High'].iloc[-1]-df_today['Low'].iloc[-1])*0.5: action = "EXIT"
        elif curr_p > ma5 and curr_vol > vol_avg * 1.2: action = "ENTRY"

        return jsonify({
            "status": "success", "name": name, "price": round(curr_p, 2), "ma20": round(ma20, 2),
            "is_above_ma20": bool(curr_p > ma20), "action": action, "date": last_day.strftime('%Y-%m-%d'),
            "intraday": {"labels": df_today.index.strftime('%H:%M').tolist(), "prices": df_today['Close'].round(2).tolist()}
        })
    except: return jsonify({"status": "error"})

if __name__ == '__main__':
    app.run(debug=True, port=5000)