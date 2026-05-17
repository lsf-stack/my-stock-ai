import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import pandas as pd
import requests
import yfinance as yf
from bs4 import BeautifulSoup
from flask import Flask, jsonify, render_template

app = Flask(__name__)

FALLBACK_POOL = [
    "3481", "2303", "6770", "2344", "2409", "2408", "2317", "2337",
    "2887", "2324", "2327", "6116", "2881", "1303", "2498", "1802",
    "3231", "2382", "6239", "2883", "2356", "2891", "2882", "2002",
    "2618", "2603", "2609", "2615", "2610", "2330", "2454", "3711",
    "3034", "2379", "2383", "3017", "2368", "3443", "1513", "1519",
    "1504", "5871", "1216", "1301", "4938", "5314", "8069", "5483",
    "3293", "2357", "3661", "2308", "2313", "2329", "2345", "2353",
    "2367", "2371", "2376", "2385", "2393", "2404", "2449", "2474",
    "2492", "2605", "2617", "2634", "2637", "2884", "2885", "2886",
    "2888", "2890", "2892", "3035", "3045", "3059", "3189", "3374",
    "3653", "3702", "4743", "4968", "5009", "6235", "6462", "6531",
    "6669", "8046", "8112", "8150", "8210", "8299", "8358", "8996",
]

cache = {
    "candidates": [],
    "monitor_pool": FALLBACK_POOL,
    "pool_source": "fallback",
    "last_scan": "--",
    "scan_status": "warming",
    "scan_progress": {"done": 0, "total": 0},
    "stock_analysis": {},
    "names": {},
    "fx": 32.2,
}


def clean_ticker(ticker):
    return ticker.strip().upper().replace(".TW", "").replace(".TWO", "")


def parse_number(value):
    try:
        return int(str(value).replace(",", "").replace("--", "0").strip())
    except Exception:
        return 0


def is_common_stock_code(code):
    return code.isdigit() and len(code) == 4 and not code.startswith("0")


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
    pure = clean_ticker(ticker)
    if pure in cache["names"]:
        return cache["names"][pure]

    try:
        url = f"https://tw.stock.yahoo.com/quote/{pure}"
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(url, headers=headers, timeout=5)
        soup = BeautifulSoup(r.text, "html.parser")
        name_tag = soup.find("h1", class_="C($c-link-text)")
        name = name_tag.text.strip() if name_tag else pure
        cache["names"][pure] = name
        return name
    except Exception:
        return pure


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


def request_json(url, params):
    headers = {"User-Agent": "Mozilla/5.0"}
    response = requests.get(url, params=params, headers=headers, timeout=15)
    response.raise_for_status()
    return response.json()


def roc_date(day):
    return f"{day.year - 1911}/{day.month:02d}/{day.day:02d}"


def extract_volume_rows(payload):
    rows = []

    if isinstance(payload, list):
        for item in payload:
            code = str(item.get("Code", "")).strip()
            if not is_common_stock_code(code):
                continue
            rows.append({
                "ticker": code,
                "name": str(item.get("Name", code)).strip(),
                "volume": parse_number(item.get("TradeVolume", 0)),
            })
        return rows

    def collect(fields, data_rows):
        if not fields or not data_rows:
            return
        code_idx = next((i for i, f in enumerate(fields) if "代號" in str(f)), None)
        name_idx = next((i for i, f in enumerate(fields) if "名稱" in str(f)), None)
        vol_idx = next((i for i, f in enumerate(fields) if "成交股數" in str(f) or "成交股" in str(f)), None)
        if code_idx is None or vol_idx is None:
            return
        for row in data_rows:
            if len(row) <= max(code_idx, vol_idx):
                continue
            code = str(row[code_idx]).strip()
            if not is_common_stock_code(code):
                continue
            rows.append({
                "ticker": code,
                "name": str(row[name_idx]).strip() if name_idx is not None and len(row) > name_idx else code,
                "volume": parse_number(row[vol_idx]),
            })

    for key, value in payload.items():
        if key.startswith("fields"):
            suffix = key.replace("fields", "")
            collect(value, payload.get(f"data{suffix}", []))

    for table in payload.get("tables", []):
        collect(table.get("fields"), table.get("data"))

    collect(payload.get("fields"), payload.get("data"))
    collect(payload.get("fields"), payload.get("aaData"))
    return rows


def fetch_twse_volume_rows(day):
    payload = request_json("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL", {})
    return extract_volume_rows(payload)


def fetch_tpex_volume_rows(day):
    endpoints = [
        (
            "https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes",
            {"response": "json", "date": roc_date(day)},
        ),
        (
            "https://www.tpex.org.tw/web/stock/aftertrading/daily_close_quotes/stk_quote_result.php",
            {"l": "zh-tw", "o": "json", "d": roc_date(day), "s": "0,asc,0"},
        ),
    ]
    for url, params in endpoints:
        try:
            rows = extract_volume_rows(request_json(url, params))
            if rows:
                return rows
        except Exception:
            continue
    return []


def fetch_top_volume_pool(limit=300):
    today = datetime.now()
    rows = []
    try:
        rows.extend(fetch_twse_volume_rows(today))
    except Exception:
        pass
    try:
        rows.extend(fetch_tpex_volume_rows(today))
    except Exception:
        pass

    merged = {}
    for row in rows:
        if row["volume"] <= 0:
            continue
        current = merged.get(row["ticker"])
        if not current or row["volume"] > current["volume"]:
            merged[row["ticker"]] = row

    ranked = sorted(merged.values(), key=lambda item: item["volume"], reverse=True)
    if ranked:
        return [row["ticker"] for row in ranked[:limit]], "official latest"

    return FALLBACK_POOL, "fallback"


def download_stock_frames(ticker):
    pure = clean_ticker(ticker)
    for suffix in [".TW", ".TWO"]:
        target = f"{pure}{suffix}"
        df_1m = yf.download(target, period="5d", interval="1m", progress=False, timeout=15)
        if not df_1m.empty:
            df_1d = yf.download(target, period="3mo", interval="1d", progress=False, timeout=15)
            if not df_1d.empty:
                return pure, target, df_1m, df_1d
    return pure, None, pd.DataFrame(), pd.DataFrame()


def cache_key(ticker, include_intraday):
    return f"{clean_ticker(ticker)}:{'full' if include_intraday else 'scan'}"


def analyze_stock(ticker, include_intraday=True):
    key = cache_key(ticker, include_intraday)
    cached = cache["stock_analysis"].get(key)
    if cached and time.time() - cached["time"] < 600:
        return cached["data"]

    pure, target, df_1m, df_1d = download_stock_frames(ticker)
    if df_1m.empty or df_1d.empty:
        return None

    last_day = df_1m.index[-1].date()
    df_today = df_1m[df_1m.index.date == last_day]
    if df_today.empty:
        return None

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
    result = {
        "status": "success",
        "ticker": pure,
        "target": target,
        "name": get_chinese_name(pure),
        "price": round(curr_p, 2),
        "ma5": round(ma5, 2),
        "ma20": round(ma20, 2),
        "rsi": round(rsi, 2) if pd.notna(rsi) else None,
        "volume_ratio": round(volume_ratio, 2),
        "ratio": round(volume_ratio, 2),
        "score": score,
        "reasons": reasons,
        "warnings": warnings,
        "is_above_ma20": bool(curr_p > ma20),
        "action": action,
        "date": last_day.strftime("%Y-%m-%d"),
    }

    if include_intraday:
        result["intraday"] = {
            "labels": df_today.index.strftime("%H:%M").tolist(),
            "prices": close_today.round(2).tolist(),
        }

    cache["stock_analysis"][key] = {"time": time.time(), "data": result}
    return result


def rank_candidate(item):
    rsi = item["rsi"] if item["rsi"] is not None else 0
    rsi_quality = 1 if 45 <= rsi <= 70 else 0
    return (
        item["score"],
        item["is_above_ma20"],
        rsi_quality,
        item["volume_ratio"],
        -abs(rsi - 57),
    )


def publish_candidates(candidates):
    candidates.sort(key=rank_candidate, reverse=True)
    cache["candidates"] = candidates[:10]


def scan_top_candidates():
    pool, pool_date = fetch_top_volume_pool(limit=100)
    cache["monitor_pool"] = pool
    cache["pool_source"] = pool_date
    cache["scan_progress"] = {"done": 0, "total": len(pool)}

    candidates = []
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(analyze_stock, ticker, False) for ticker in pool]
        for future in as_completed(futures):
            cache["scan_progress"]["done"] += 1
            try:
                item = future.result()
                if item and item["score"] >= 2 and item["is_above_ma20"]:
                    candidates.append(item)
                    publish_candidates(candidates)
            except Exception:
                continue

    publish_candidates(candidates)
    return cache["candidates"]


def background_scanner():
    while True:
        cache["scan_status"] = "scanning"
        try:
            cache["candidates"] = scan_top_candidates()
            cache["last_scan"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            cache["scan_status"] = "ready"
        except Exception:
            cache["scan_status"] = "error"
        time.sleep(600)


threading.Thread(target=background_scanner, daemon=True).start()


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/market")
def market_api():
    try:
        ntd = yf.download("TWD=X", period="2d", interval="1d", progress=False, timeout=10)
        if not ntd.empty:
            cache["fx"] = round(scalar(column(ntd, "Close").iloc[-1]), 2)
    except Exception:
        pass

    return jsonify({
        "status": "success",
        "fx": cache["fx"],
        "spikes": cache["candidates"],
        "candidates": cache["candidates"],
        "pool_size": len(cache["monitor_pool"]),
        "pool_source": cache["pool_source"],
        "scan_progress": cache["scan_progress"],
        "last_scan": cache["last_scan"],
        "scan_status": cache["scan_status"],
    })


@app.route("/api/stock/<ticker>")
def stock_api(ticker):
    try:
        data = analyze_stock(ticker, include_intraday=True)
        if not data:
            return jsonify({"status": "error"})
        return jsonify(data)
    except Exception:
        return jsonify({"status": "error"})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
