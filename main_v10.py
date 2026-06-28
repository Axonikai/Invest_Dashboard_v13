"""
AXONIK Trading Backend v2 — API Unificada
El frontend solo habla con esta API. Cero CORS issues.
"""
import logging
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import pytz

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler("logs/axonik.log")]
)
logger = logging.getLogger(__name__)
MADRID_TZ = pytz.timezone("Europe/Madrid")

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("AXONIK API v2 arrancando...")
    from scripts.scheduler import start_scheduler
    start_scheduler()
    logger.info("✅ API lista")
    yield
    from scripts.scheduler import stop_scheduler
    stop_scheduler()

app = FastAPI(title="AXONIK Investment Intelligence API", version="2.0.0", lifespan=lifespan)

app.add_middleware(CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)

# ─── HEALTH ───────────────────────────────────────────────
@app.get("/health")
def health():
    return {
        "status": "ok",
        "version": "2.0.0",
        "time_madrid": datetime.now(MADRID_TZ).strftime("%Y-%m-%d %H:%M:%S")
    }

@app.get("/market/status")
def market_status():
    from app.data import is_market_open, is_premarket, is_axonik_window
    return {
        "market_open": is_market_open(),
        "premarket": is_premarket(),
        "axonik_window": is_axonik_window(),
        "time_madrid": datetime.now(MADRID_TZ).strftime("%H:%M"),
        "crypto_always_open": True
    }

# ─── OHLCV CON TIMEFRAME DINÁMICO ─────────────────────────
@app.get("/ohlcv/{ticker}")
def get_ohlcv(ticker: str, tf: str = "1d", limit: int = 200):
    """
    OHLCV con soporte de timeframe dinámico.
    Cripto: Binance (24/7). Acciones: yfinance.
    tf: 5m | 15m | 1h | 4h | 1d | 1w
    """
    from app.api_modules import get_crypto_ohlcv, get_stock_ohlcv
    from app.data import IS_CRYPTO_MAP

    ticker = ticker.upper()
    is_crypto = ticker in IS_CRYPTO_MAP

    if is_crypto:
        data = get_crypto_ohlcv(ticker, tf, limit)
    else:
        data = get_stock_ohlcv(ticker, tf)

    if "error" in data and not data.get("candles"):
        raise HTTPException(status_code=404, detail=data["error"])

    return data

# ─── QUOTES WATCHLIST ─────────────────────────────────────
@app.get("/quotes")
def get_quotes(symbols: str = "NVDA,AAPL,MSFT,AMD,META,TSLA,AMZN,GOOGL,PLTR,CRM"):
    """Precios en tiempo real para watchlist. yfinance sin CORS."""
    import yfinance as yf
    tickers = [s.strip().upper() for s in symbols.split(",")][:15]
    result = []
    try:
        dl = tickers[0] if len(tickers)==1 else tickers
        data = yf.download(dl, period="5d", interval="1d", progress=False,
                           auto_adjust=True, group_by="ticker" if len(tickers)>1 else None)
        for t in tickers:
            try:
                col = data[t]["Close"] if len(tickers)>1 else data["Close"]
                clean = col.dropna()
                ct = float(clean.iloc[-1])
                cp = float(clean.iloc[-2]) if len(clean)>1 else ct
                chg = round((ct-cp)/cp*100, 2) if cp else 0
                result.append({"symbol": t, "price": round(ct,2), "changesPercentage": chg, "name": t})
            except:
                result.append({"symbol": t, "price": 0, "changesPercentage": 0, "name": t})
    except Exception as e:
        logger.error(f"Quotes error: {e}")
    return {"quotes": result}

# ─── WATCHLIST DINÁMICA ───────────────────────────────────
@app.get("/watchlist/{category}")
def get_watchlist(category: str = "momentum"):
    """
    Watchlist dinámica por categoría.
    categories: momentum | gappers | crypto | etf
    """
    from app.api_modules import get_momentum_stocks, get_top_gappers, get_crypto_watchlist
    if category == "momentum":
        return {"category": category, "items": get_momentum_stocks()}
    elif category == "gappers":
        return {"category": category, "items": get_top_gappers()}
    elif category == "crypto":
        return {"category": category, "items": get_crypto_watchlist()}
    else:
        return {"category": category, "items": get_momentum_stocks()}

# ─── FUNDAMENTAL ESTRUCTURADO ─────────────────────────────
@app.get("/fundamental/{ticker}")
def get_fundamental(ticker: str):
    """Datos fundamentales estructurados con health scores."""
    from app.api_modules import get_fundamental_structured
    return get_fundamental_structured(ticker.upper())

# ─── NOTICIAS CON IMPACTO ─────────────────────────────────
@app.get("/news/{ticker}")
def get_news(ticker: str, limit: int = 10):
    """Noticias con clasificación de sentimiento e impacto."""
    from app.api_modules import get_news_with_impact
    return {"ticker": ticker.upper(), "news": get_news_with_impact(ticker.upper(), limit)}

# ─── INSIDERS CON HISTÓRICO ───────────────────────────────
@app.get("/insiders/{ticker}")
def get_insiders(ticker: str):
    """Insiders — siempre muestra histórico, nunca vacío."""
    from app.api_modules import get_insiders_with_history
    return get_insiders_with_history(ticker.upper())

# ─── TICKER COMPLETO (un solo endpoint para todo) ─────────
@app.get("/ticker/{ticker}")
def get_ticker(ticker: str, period: str = "90d", interval: str = "1d"):
    """Datos técnicos completos del ticker."""
    from app.data import get_ohlcv, add_indicators
    ticker = ticker.upper()
    df = get_ohlcv(ticker, period=period, interval=interval)
    if df is None or df.empty:
        raise HTTPException(status_code=404, detail=f"No data for {ticker}")
    df = add_indicators(df)
    last = df.iloc[-1]
    def safe_float(v):
        try:
            f = float(v)
            return round(f, 4) if abs(f) < 1e6 else round(f, 2)
        except: return None
    return {
        "ticker": ticker,
        "rows": len(df),
        "last": {
            "price": safe_float(last.get("close")),
            "ema20": safe_float(last.get("ema20")),
            "ema50": safe_float(last.get("ema50")),
            "ema200": safe_float(last.get("ema200")),
            "rsi": safe_float(last.get("rsi")),
            "macd": safe_float(last.get("macd")),
            "macd_signal": safe_float(last.get("macd_signal")),
            "rvol": safe_float(last.get("rvol")),
            "atr_pct": safe_float(last.get("atr_pct")),
            "above_ema20": bool(last.get("close", 0) > last.get("ema20", 0)),
            "ema_stack_bull": bool(last.get("ema20", 0) > last.get("ema50", 0)),
        }
    }

# ─── SCANNER ─────────────────────────────────────────────
@app.get("/scan")
@app.post("/scan")
def run_scan(min_score: int = 45):
    """Scanner MTF completo."""
    from app.data import DEFAULT_UNIVERSE, CRYPTO_UNIVERSE
    from scanner.strategies import run_scanner
    start = time.time()
    universe = DEFAULT_UNIVERSE + CRYPTO_UNIVERSE
    results = run_scanner(universe, min_score=min_score)
    elapsed = time.time() - start
    return {
        "scan_time": round(elapsed, 2),
        "tickers_scanned": len(universe),
        "setups_found": len(results),
        "results": [r.to_dict() for r in results],
        "timestamp": datetime.now(MADRID_TZ).strftime("%Y-%m-%d %H:%M:%S"),
    }

@app.post("/scan/trigger")
def trigger_scan():
    """Dispara scan en background + alertas Telegram."""
    from scripts.scheduler import job_scanner
    import threading
    threading.Thread(target=job_scanner, daemon=True).start()
    return {"status": "triggered"}

@app.post("/premarket/trigger")
def trigger_premarket():
    from scripts.scheduler import job_premarket_check
    import threading
    threading.Thread(target=job_premarket_check, daemon=True).start()
    return {"status": "triggered"}


@app.get("/screener")
async def get_screener(
    symbols: str = "NVDA,AAPL,MSFT,AMD,META,TSLA,AMZN,GOOGL,PLTR,CRM,NOW,SNOW,CRWD,DDOG,NET,PANW,AVGO,ARM,MSTR,COIN,HOOD,IBIT,MARA,RIOT"
):
    """Screener con indicadores técnicos reales — funciona siempre (fin de semana incluido)."""
    import yfinance as yf
    import numpy as np

    tickers = [s.strip().upper() for s in symbols.split(",")][:30]
    results = []
    try:
        raw = yf.download(tickers if len(tickers)>1 else tickers[0],
                         period="60d", interval="1d",
                         progress=False, auto_adjust=True,
                         group_by="ticker" if len(tickers)>1 else None)
    except Exception as e:
        logger.error(f"Screener error: {e}")
        return {"results": [], "error": str(e)}

    def calc_ema(arr, p):
        if len(arr) < p: return float(arr[-1]) if len(arr) else 0
        k = 2/(p+1); e = float(arr[:p].mean())
        for v in arr[p:]: e = float(v)*k + e*(1-k)
        return round(e, 2)

    def calc_rsi(arr, p=14):
        if len(arr) < p+1: return 50.0
        d = np.diff(arr.astype(float))
        g = np.where(d>0,d,0); l = np.where(d<0,-d,0)
        ag = g[-p:].mean(); al = l[-p:].mean()
        return round(100-100/(1+ag/al) if al>0 else 100.0, 1)

    for ticker in tickers:
        try:
            df = raw[ticker] if len(tickers)>1 else raw
            df = df.dropna(subset=["Close"])
            if len(df) < 20:
                results.append({"symbol": ticker, "price": 0, "change_pct": 0, "rvol": 0, "rsi": 50, "ema_bull": False, "score": 0, "trend": "Sin datos"})
                continue
            closes = df["Close"].values; vols = df["Volume"].values if "Volume" in df else np.ones(len(closes))
            price = round(float(closes[-1]),2); prev = float(closes[-2]) if len(closes)>1 else price
            chg = round((price-prev)/prev*100,2) if prev else 0
            ema20 = calc_ema(closes,20); ema50 = calc_ema(closes,50)
            rsi_v = calc_rsi(closes)
            vol_avg = float(vols[-21:-1].mean()) if len(vols)>21 else float(vols.mean())
            rvol = round(float(vols[-1])/vol_avg,2) if vol_avg>0 else 1.0
            ema_bull = bool(price>ema20 and ema20>ema50)
            score = 30
            if price>ema20: score+=15
            if ema20>ema50: score+=15
            if 50<=rsi_v<=65: score+=20
            elif 40<=rsi_v<50: score+=8
            if rvol>=1.5: score+=20
            elif rvol>=1.0: score+=8
            if chg>0: score+=10
            score=min(100,score)
            results.append({"symbol":ticker,"price":price,"change_pct":chg,"rvol":rvol,"rsi":rsi_v,"ema20":ema20,"ema50":ema50,"ema_bull":ema_bull,"score":score,"trend":"Alcista" if ema_bull else "Bajista"})
        except Exception as e:
            logger.error(f"Screener {ticker}: {e}")
            results.append({"symbol":ticker,"price":0,"change_pct":0,"rvol":0,"rsi":50,"ema_bull":False,"score":0,"trend":"Error"})

    results.sort(key=lambda x: x.get("score",0), reverse=True)
    return {"count":len(results),"timestamp":datetime.now(MADRID_TZ).strftime("%Y-%m-%d %H:%M:%S"),"results":results}

@app.get("/scheduler/jobs")
def get_jobs():
    from scripts.scheduler import _scheduler
    if not _scheduler:
        return {"jobs": [], "running": False}
    return {
        "jobs": [{"id": j.id, "name": j.name, "next_run": str(j.next_run_time)} for j in _scheduler.get_jobs()],
        "running": _scheduler.running
    }

@app.get("/universe")
def get_universe():
    from app.data import DEFAULT_UNIVERSE, CRYPTO_UNIVERSE
    return {"equities": DEFAULT_UNIVERSE, "crypto": CRYPTO_UNIVERSE}
