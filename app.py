import yfinance as yf
import pandas as pd
import requests
from bs4 import BeautifulSoup
from flask import Flask, render_template, jsonify
from datetime import datetime
import os

app = Flask(__name__)

# 偽裝瀏覽器標頭，防止雲端 IP 被封鎖
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
}

# 全局緩存匯率，若抓不到就用舊的
global_cache = {"fx": 32.1, "spikes": []}

def get_chinese_name(ticker):
    try:
        url = f"https://tw.stock.yahoo.com/quote/{ticker}"
        r = requests.get(url, headers=HEADERS, timeout=5)
        soup = BeautifulSoup(r.text, 'html.parser')
        name_tag = soup.find('h1', class_='C($c-link-text)')
        return name_tag.text.strip() if name_tag else f"個股 {ticker}"
    except: return f"個股 {ticker}"

@app.route('/')
def index(): return render_template('index.html')

@app.route('/api/market')
def market_api():
    try:
        # 雲端版：使用 User-Agent 抓取匯率
        ntd = yf.download("TWD=X", period="1d", progress=False, timeout=10)
        if not ntd.empty:
            global_cache["fx"] = round(ntd['Close'].iloc[-1], 2)
        return jsonify({
            "status": "success",
            "fx": global_cache["fx"],
            "spikes": [], # 免費版暫停背景掃描以求 API 穩定
            "last_scan": datetime.now().strftime("%H:%M:%S")
        })
    except:
        return jsonify({"status": "success", "fx": global_cache["fx"], "spikes": []})

@app.route('/api/stock/<ticker>')
def stock_api(ticker):
    try:
        df_1m = pd.DataFrame()
        df_1d = pd.DataFrame()
        
        # 雲端版的核心修正：顯式嘗試上市櫃後綴
        for suffix in [".TW", ".TWO"]:
            target = f"{ticker}{suffix}"
            # 使用 yf.download 搭配 timeout，這在 Render 上最穩
            df_1m = yf.download(target, period="5d", interval="1m", progress=False, timeout=15)
            if not df_1m.empty:
                df_1d = yf.download(target, period="3mo", interval="1d", progress=False, timeout=15)
                break
        
        if df_1m.empty:
            return jsonify({"status": "error", "message": "Yahoo IP 暫時封鎖中，請稍後再試"})

        last_day = df_1m.index[-1].date()
        df_today = df_1m[df_1m.index.date == last_day]
        ma20 = df_1d['Close'].rolling(window=20).mean().iloc[-1]
        curr_p = df_today['Close'].iloc[-1]
        ma5 = df_today['Close'].tail(5).mean()
        
        action = "ENTRY" if curr_p > ma5 * 1.002 else "EXIT" if curr_p < ma5 * 0.998 else "HOLD"

        return jsonify({
            "status": "success",
            "name": get_chinese_name(ticker),
            "price": round(curr_p, 2),
            "ma20": round(ma20, 2),
            "is_above_ma20": bool(curr_p > ma20),
            "action": action,
            "date": last_day.strftime('%Y-%m-%d'),
            "intraday": {
                "labels": df_today.index.strftime('%H:%M').tolist(),
                "prices": df_today['Close'].round(2).tolist()
            }
        })
    except Exception as e:
        print(f"Stock Error: {e}")
        return jsonify({"status": "error"})

if __name__ == '__main__':
    # 支援 Render 分配的 PORT
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)