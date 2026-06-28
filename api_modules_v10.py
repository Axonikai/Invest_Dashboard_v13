"""
AXONIK — Módulos de API unificados
Sirven datos al frontend eliminando CORS y rate limits
"""
import yfinance as yf
import requests
import pandas as pd
import numpy as np
import logging
from typing import Optional
from datetime import datetime, timedelta
import pytz

logger = logging.getLogger(__name__)
MADRID_TZ = pytz.timezone("Europe/Madrid")

FMP_KEY = "F6o0eDuRAmBhWCzepJnmt1j3ZfvMqaVu"
AV_KEY  = "TMPCW6375SJ9JYNI"

# ─── OHLCV con timeframe dinámico ─────────────────────────
BINANCE_TF_MAP = {
    "5m": "5m", "15m": "15m", "1h": "1h", "4h": "4h", "1d": "1d", "1w": "1w"
}
YF_TF_MAP = {
    "5m":  ("5d",   "5m"),
    "15m": ("10d",  "15m"),
    "1h":  ("30d",  "1h"),
    "4h":  ("60d",  "1h"),   # yfinance no tiene 4H nativo, se resamplea
    "1d":  ("2y",   "1d"),
    "1w":  ("5y",   "1wk"),
}

def get_crypto_ohlcv(ticker: str, tf: str = "1d", limit: int = 200) -> dict:
    """OHLCV de Binance para cripto. Siempre disponible 24/7."""
    symbol = ticker.upper() + "USDT"
    interval = BINANCE_TF_MAP.get(tf, "1d")
    try:
        url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
        r = requests.get(url, timeout=8)
        if r.status_code != 200:
            return {"error": f"Binance error {r.status_code}", "candles": []}
        raw = r.json()
        candles = []
        for k in raw:
            t = int(k[0]) // 1000
            o, h, l, c, v = float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])
            if all(np.isfinite(x) for x in [t, o, h, l, c]):
                candles.append({"time": t, "open": o, "high": h, "low": l, "close": c, "volume": v})
        # Ticker price data
        tick_url = f"https://api.binance.com/api/v3/ticker/24hr?symbol={symbol}"
        tr = requests.get(tick_url, timeout=5).json()
        return {
            "ticker": ticker,
            "symbol": symbol,
            "tf": tf,
            "candles": candles,
            "price": float(tr.get("lastPrice", 0)),
            "change_pct": float(tr.get("priceChangePercent", 0)),
            "volume_24h": float(tr.get("quoteVolume", 0)),
            "high_24h": float(tr.get("highPrice", 0)),
            "low_24h": float(tr.get("lowPrice", 0)),
        }
    except Exception as e:
        logger.error(f"Crypto OHLCV error {ticker}: {e}")
        return {"error": str(e), "candles": []}

def get_stock_ohlcv(ticker: str, tf: str = "1d") -> dict:
    """OHLCV de yfinance para acciones con soporte de timeframe."""
    period, interval = YF_TF_MAP.get(tf, ("120d", "1d"))
    try:
        data = yf.download(ticker, period=period, interval=interval,
                          progress=False, auto_adjust=True)
        if data is None or data.empty:
            return {"error": "No data", "candles": []}

        # Fix MultiIndex yfinance
        if isinstance(data.columns, pd.MultiIndex):
            data.columns = [col[0].lower() for col in data.columns]
        else:
            data.columns = [str(c).lower() for c in data.columns]

        # Resample 4H si es necesario
        if tf == "4h":
            data = data.resample("4h").agg({
                "open": "first", "high": "max", "low": "min",
                "close": "last", "volume": "sum"
            }).dropna()

        candles = []
        for idx, row in data.iterrows():
            try:
                t = int(pd.Timestamp(idx).timestamp())
                o, h, l, c = float(row.get("open", row["close"])), float(row.get("high", row["close"])), float(row.get("low", row["close"])), float(row["close"])
                v = float(row.get("volume", 0))
                if all(np.isfinite(x) for x in [t, o, h, l, c]):
                    candles.append({"time": t, "open": o, "high": h, "low": l, "close": c, "volume": v})
            except Exception:
                continue

        # Precio actual
        price = candles[-1]["close"] if candles else 0
        prev  = candles[-2]["close"] if len(candles) > 1 else price
        chg   = round((price - prev) / prev * 100, 2) if prev else 0

        return {
            "ticker": ticker,
            "tf": tf,
            "candles": candles,
            "price": round(price, 2),
            "change_pct": chg,
        }
    except Exception as e:
        logger.error(f"Stock OHLCV error {ticker}: {e}")
        return {"error": str(e), "candles": []}

# ─── WATCHLIST DINÁMICA ───────────────────────────────────
def get_momentum_stocks(limit: int = 15) -> list:
    """Top acciones por momentum (cambio % hoy + volumen relativo)."""
    universe = [
        "NVDA","AAPL","MSFT","AMD","META","TSLA","AMZN","GOOGL","PLTR","CRM",
        "NOW","SNOW","CRWD","DDOG","NET","PANW","AVGO","ARM","MSTR","COIN",
        "HOOD","RXRX","IBIT","MARA","RIOT","SMCI","INTC","NFLX","UBER","LYFT"
    ]
    results = []
    try:
        symbols = ",".join(universe)
        url = f"https://financialmodelingprep.com/stable/batch-quotes?symbols={symbols}&apikey={FMP_KEY}"
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, list):
                sorted_data = sorted(data, key=lambda x: abs(x.get("changesPercentage", 0)), reverse=True)
                for q in sorted_data[:limit]:
                    results.append({
                        "symbol": q.get("symbol", ""),
                        "name": (q.get("name", "") or "").split(" ")[0],
                        "price": round(q.get("price", 0), 2),
                        "change_pct": round(q.get("changesPercentage", 0), 2),
                        "volume": q.get("volume", 0),
                        "category": "momentum"
                    })
    except Exception as e:
        logger.error(f"Momentum stocks error: {e}")
    return results

def get_top_gappers(limit: int = 10) -> list:
    """Acciones con mayor gap % respecto al cierre anterior."""
    universe = ["NVDA","AMD","TSLA","AAPL","META","AMZN","MSFT","GOOGL","PLTR","CRM",
                "COIN","MSTR","HOOD","SMCI","RXRX","CRWD","NET","SNOW","DDOG","PANW"]
    results = []
    try:
        symbols = ",".join(universe)
        url = f"https://financialmodelingprep.com/stable/batch-quotes?symbols={symbols}&apikey={FMP_KEY}"
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, list):
                gappers = [q for q in data if abs(q.get("changesPercentage", 0)) > 1.5]
                gappers.sort(key=lambda x: abs(x.get("changesPercentage", 0)), reverse=True)
                for q in gappers[:limit]:
                    results.append({
                        "symbol": q.get("symbol", ""),
                        "name": (q.get("name", "") or "").split(" ")[0],
                        "price": round(q.get("price", 0), 2),
                        "change_pct": round(q.get("changesPercentage", 0), 2),
                        "volume": q.get("volume", 0),
                        "category": "gapper"
                    })
    except Exception as e:
        logger.error(f"Gappers error: {e}")
    return results

def get_crypto_watchlist() -> list:
    """Top cripto por volumen 24h via Binance."""
    symbols = ["BTCUSDT","ETHUSDT","SOLUSDT","BNBUSDT","XRPUSDT","ADAUSDT","DOGEUSDT","AVAXUSDT","LINKUSDT","DOTUSDT"]
    results = []
    try:
        for sym in symbols:
            r = requests.get(f"https://api.binance.com/api/v3/ticker/24hr?symbol={sym}", timeout=5)
            if r.status_code == 200:
                d = r.json()
                ticker = sym.replace("USDT", "")
                results.append({
                    "symbol": ticker,
                    "name": ticker,
                    "price": round(float(d.get("lastPrice", 0)), 6 if float(d.get("lastPrice", 0)) < 1 else 2),
                    "change_pct": round(float(d.get("priceChangePercent", 0)), 2),
                    "volume": round(float(d.get("quoteVolume", 0))),
                    "category": "crypto"
                })
    except Exception as e:
        logger.error(f"Crypto watchlist error: {e}")
    return results

# ─── FUNDAMENTAL ESTRUCTURADO ─────────────────────────────
def get_fundamental_structured(ticker: str) -> dict:
    """Datos fundamentales estructurados con health scores."""
    result = {
        "ticker": ticker,
        "valuation": {}, "profitability": {}, "growth": {},
        "financial_health": {}, "efficiency": {}, "health_scores": {}
    }
    try:
        # Alpha Vantage overview
        url = f"https://www.alphavantage.co/query?function=OVERVIEW&symbol={ticker}&apikey={AV_KEY}"
        r = requests.get(url, timeout=8)
        if r.status_code == 200:
            av = r.json()
            if av and "Symbol" in av:
                pe = float(av.get("PERatio", 0) or 0)
                pb = float(av.get("PriceToBookRatio", 0) or 0)
                roe = float(av.get("ReturnOnEquityTTM", 0) or 0)
                roa = float(av.get("ReturnOnAssetsTTM", 0) or 0)
                margin = float(av.get("ProfitMargin", 0) or 0)
                op_margin = float(av.get("OperatingMarginTTM", 0) or 0)
                rev_growth = float(av.get("QuarterlyRevenueGrowthYOY", 0) or 0)
                eps_growth = float(av.get("QuarterlyEarningsGrowthYOY", 0) or 0)
                debt_equity = float(av.get("DebtToEquityRatio", 0) or 0)
                beta = float(av.get("Beta", 0) or 0)

                result["valuation"] = {
                    "pe_ratio": round(pe, 2),
                    "pb_ratio": round(pb, 2),
                    "ev_ebitda": float(av.get("EVToEBITDA", 0) or 0),
                    "ps_ratio": float(av.get("PriceToSalesRatioTTM", 0) or 0),
                    "dividend_yield": float(av.get("DividendYield", 0) or 0),
                    "market_cap": float(av.get("MarketCapitalization", 0) or 0),
                }
                result["profitability"] = {
                    "roe": round(roe * 100, 1),
                    "roa": round(roa * 100, 1),
                    "net_margin": round(margin * 100, 1),
                    "operating_margin": round(op_margin * 100, 1),
                    "ebitda": float(av.get("EBITDA", 0) or 0),
                    "eps": float(av.get("EPS", 0) or 0),
                }
                result["growth"] = {
                    "revenue_growth_yoy": round(rev_growth * 100, 1),
                    "eps_growth_yoy": round(eps_growth * 100, 1),
                }
                result["financial_health"] = {
                    "debt_to_equity": round(debt_equity, 2),
                    "beta": round(beta, 2),
                    "52w_high": float(av.get("52WeekHigh", 0) or 0),
                    "52w_low": float(av.get("52WeekLow", 0) or 0),
                }
                result["meta"] = {
                    "name": av.get("Name", ticker),
                    "sector": av.get("Sector", "N/A"),
                    "industry": av.get("Industry", "N/A"),
                    "description": (av.get("Description", "") or "")[:300],
                }

                # Health scores (0-100) calculados
                val_score = max(0, min(100, 50 + (30 - pe) * 1.5)) if pe > 0 else 50
                prof_score = max(0, min(100, roe * 100 * 3 + margin * 100 * 2))
                growth_score = max(0, min(100, 50 + rev_growth * 200))
                health_score = max(0, min(100, 100 - debt_equity * 20))

                result["health_scores"] = {
                    "valuation": round(val_score),
                    "profitability": round(prof_score),
                    "growth": round(growth_score),
                    "financial_health": round(health_score),
                    "overall": round((val_score + prof_score + growth_score + health_score) / 4),
                }
    except Exception as e:
        logger.error(f"Fundamental error {ticker}: {e}")
    return result

# ─── NOTICIAS CON IMPACTO ─────────────────────────────────
def get_news_with_impact(ticker: str, limit: int = 10) -> list:
    """Noticias con clasificación de impacto y horizonte temporal."""
    results = []
    try:
        url = f"https://financialmodelingprep.com/stable/news/stock?symbols={ticker}&limit={limit}&apikey={FMP_KEY}"
        r = requests.get(url, timeout=8)
        if r.status_code == 200:
            news = r.json()
            if isinstance(news, list):
                for n in news:
                    title = n.get("title", "") or ""
                    # Clasificación de impacto por NLP básico
                    bullish_words = ["beat", "surge", "jump", "record", "strong", "upgrade", "buy", "growth", "profit", "raise", "outperform"]
                    bearish_words = ["miss", "fall", "drop", "cut", "weak", "downgrade", "sell", "loss", "decline", "warning", "disappoint"]
                    title_lower = title.lower()
                    b_count = sum(1 for w in bullish_words if w in title_lower)
                    be_count = sum(1 for w in bearish_words if w in title_lower)

                    if b_count > be_count:
                        sentiment = "bullish"
                        impact = "HIGH" if b_count >= 2 else "MEDIUM"
                    elif be_count > b_count:
                        sentiment = "bearish"
                        impact = "HIGH" if be_count >= 2 else "MEDIUM"
                    else:
                        sentiment = "neutral"
                        impact = "LOW"

                    results.append({
                        "title": title,
                        "source": n.get("site", ""),
                        "date": n.get("publishedDate", ""),
                        "url": n.get("url", ""),
                        "sentiment": sentiment,
                        "impact": impact,
                        "horizon": "24h" if b_count + be_count >= 3 else "7d",
                    })
    except Exception as e:
        logger.error(f"News error {ticker}: {e}")
    return results

# ─── INSIDERS CON HISTÓRICO ───────────────────────────────
def get_insiders_with_history(ticker: str, limit: int = 15) -> dict:
    """Insiders con histórico — nunca vacío, siempre muestra últimos datos conocidos."""
    result = {"ticker": ticker, "transactions": [], "summary": {}}
    try:
        url = f"https://financialmodelingprep.com/stable/insider-trading?symbol={ticker}&limit={limit}&apikey={FMP_KEY}"
        r = requests.get(url, timeout=8)
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, list):
                buys, sells, buy_val, sell_val = 0, 0, 0, 0
                for item in data:
                    is_buy = item.get("transactionType", "") == "P-Purchase"
                    val = (item.get("price", 0) or 0) * (item.get("securitiesTransacted", 0) or 0)
                    if is_buy:
                        buys += 1; buy_val += val
                    else:
                        sells += 1; sell_val += val
                    result["transactions"].append({
                        "name": item.get("reportingName", ""),
                        "title": item.get("typeOfOwner", ""),
                        "date": item.get("filingDate", ""),
                        "type": "buy" if is_buy else "sell",
                        "shares": item.get("securitiesTransacted", 0),
                        "price": item.get("price", 0),
                        "value": round(val),
                    })
                # Señal neta de insiders
                net_signal = "bullish" if buys > sells else "bearish" if sells > buys else "neutral"
                result["summary"] = {
                    "total_buys": buys, "total_sells": sells,
                    "buy_value": round(buy_val), "sell_value": round(sell_val),
                    "net_signal": net_signal,
                    "note": "Datos más recientes disponibles" if data else "Sin actividad reciente"
                }
    except Exception as e:
        logger.error(f"Insiders error {ticker}: {e}")
    return result
