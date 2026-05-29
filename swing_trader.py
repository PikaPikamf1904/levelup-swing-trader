"""
LevelUp Swing Trader

A free daily swing-trading paper simulator.

Core idea:
This is not real trading. It uses fake money to test whether a rules-based
swing strategy would have worked over time.

Data source:
Alpha Vantage TIME_SERIES_DAILY.

Free-tier warning:
Alpha Vantage free plans have rate limits. Keep the watchlist tight if needed.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple, Any

import pandas as pd
import requests
from bokeh.embed import file_html
from bokeh.layouts import column
from bokeh.models import HoverTool
from bokeh.plotting import figure
from bokeh.resources import CDN


ROOT = Path(__file__).resolve().parent
PORTFOLIO_PATH = ROOT / "portfolio.json"
DATA_DIR = ROOT / "data"
DOCS_DIR = ROOT / "docs"
DATA_DIR.mkdir(exist_ok=True)
DOCS_DIR.mkdir(exist_ok=True)


@dataclass
class Signal:
    symbol: str
    score: float
    recommendation: str
    close: float
    rsi14: float
    momentum_20d: float
    momentum_60d: float
    volume_ratio: float
    trend_score: float
    learning_bonus: float
    reason: str


def load_portfolio() -> Dict[str, Any]:
    with PORTFOLIO_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_portfolio(portfolio: Dict[str, Any]) -> None:
    with PORTFOLIO_PATH.open("w", encoding="utf-8") as f:
        json.dump(portfolio, f, indent=2)


def fetch_alpha_vantage(symbol: str, api_key: str) -> pd.DataFrame:
    url = "https://www.alphavantage.co/query"
    params = {
        "function": "TIME_SERIES_DAILY",
        "symbol": symbol,
        "outputsize": "compact",
        "apikey": api_key,
        "datatype": "json",
    }
    response = requests.get(url, params=params, timeout=30)
    response.raise_for_status()
    payload = response.json()

    if "Note" in payload:
        raise RuntimeError(f"Alpha Vantage rate limit hit: {payload['Note']}")
    if "Information" in payload:
        raise RuntimeError(f"Alpha Vantage info for {symbol}: {payload['Information']}")
    if "Error Message" in payload:
        raise RuntimeError(f"Alpha Vantage error for {symbol}: {payload['Error Message']}")
    if "Time Series (Daily)" not in payload:
        raise RuntimeError(f"Unexpected response for {symbol}: {payload}")

    raw = payload["Time Series (Daily)"]
    df = pd.DataFrame.from_dict(raw, orient="index")
    df = df.rename(columns={
        "1. open": "open",
        "2. high": "high",
        "3. low": "low",
        "4. close": "close",
        "5. volume": "volume",
    })
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["adj_close"] = df["close"]
    df.index = pd.to_datetime(df.index)
    df = df.sort_index()
    return df.dropna(subset=["adj_close"])


def cache_path(symbol: str) -> Path:
    return DATA_DIR / f"{symbol}.csv"


def get_price_data(symbol: str, api_key: str) -> pd.DataFrame:
    path = cache_path(symbol)
    try:
        df = fetch_alpha_vantage(symbol, api_key)
        df.to_csv(path)
        return df
    except Exception as exc:
        if path.exists():
            print(f"Using cached data for {symbol}. Fetch failed: {exc}")
            return pd.read_csv(path, index_col=0, parse_dates=True)
        raise


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()
    rs = avg_gain / avg_loss.replace(0, pd.NA)
    return 100 - (100 / (1 + rs))


def pct_change_from(df: pd.DataFrame, days: int) -> float:
    if len(df) <= days:
        return 0.0
    return float((df["adj_close"].iloc[-1] / df["adj_close"].iloc[-days] - 1) * 100)


def learning_bonus(symbol: str, closed_trades: List[Dict[str, Any]]) -> float:
    symbol_trades = [t for t in closed_trades if t.get("symbol") == symbol]
    if not symbol_trades:
        return 0.0

    recent = symbol_trades[-6:]
    wins = [t for t in recent if t.get("pnl", 0) > 0]
    avg_pnl_pct = sum(t.get("pnl_pct", 0) for t in recent) / len(recent)

    bonus = 0.0
    bonus += min(6.0, max(-6.0, avg_pnl_pct / 2))
    bonus += min(4.0, len(wins) / len(recent) * 4)
    return round(bonus, 2)


def score_symbol(symbol: str, df: pd.DataFrame, closed_trades: List[Dict[str, Any]]) -> Signal:
    close = float(df["adj_close"].iloc[-1])
    ma10 = df["adj_close"].rolling(10).mean().iloc[-1]
    ma20 = df["adj_close"].rolling(20).mean().iloc[-1]
    ma50 = df["adj_close"].rolling(50).mean().iloc[-1]

    rsi14 = float(rsi(df["adj_close"], 14).iloc[-1])
    momentum_20d = pct_change_from(df, 20)
    momentum_60d = pct_change_from(df, 60)

    avg_vol_20 = df["volume"].rolling(20).mean().iloc[-1]
    volume_ratio = float(df["volume"].iloc[-1] / avg_vol_20) if avg_vol_20 else 1.0

    score = 0.0
    reasons = []

    trend_score = 0.0
    if close > ma20:
        trend_score += 12
        reasons.append("above 20-day trend")
    if close > ma50:
        trend_score += 12
        reasons.append("above 50-day trend")
    if ma10 > ma20:
        trend_score += 8
        reasons.append("short trend rising")
    if ma20 > ma50:
        trend_score += 8
        reasons.append("medium trend rising")
    score += trend_score

    if momentum_20d > 0:
        score += min(18, momentum_20d * 1.4)
        reasons.append("positive 20-day momentum")
    else:
        score += max(-14, momentum_20d)

    if momentum_60d > 0:
        score += min(14, momentum_60d * 0.6)
        reasons.append("positive 60-day momentum")
    else:
        score += max(-10, momentum_60d * 0.5)

    if 45 <= rsi14 <= 68:
        score += 14
        reasons.append("RSI in healthy swing zone")
    elif 68 < rsi14 <= 75:
        score += 5
        reasons.append("strong but slightly extended")
    elif rsi14 > 75:
        score -= 8
        reasons.append("overextended")
    elif rsi14 < 40:
        score -= 10
        reasons.append("weak RSI")

    if 1.1 <= volume_ratio <= 2.5:
        score += 10
        reasons.append("volume confirmation")
    elif volume_ratio > 2.5:
        score += 4
        reasons.append("volume spike")
    else:
        score += 2

    daily_vol = df["adj_close"].pct_change().tail(20).std() * 100
    if daily_vol <= 3:
        score += 6
        reasons.append("controlled volatility")
    elif daily_vol > 5:
        score -= 8
        reasons.append("high volatility")

    lb = learning_bonus(symbol, closed_trades)
    score += lb
    score = round(max(0, min(100, score)), 2)

    if score >= 80:
        rec = "Strong Watch / Sim Buy"
    elif score >= 72:
        rec = "Watch / Buy Candidate"
    elif score >= 60:
        rec = "Neutral Watch"
    else:
        rec = "Avoid"

    return Signal(symbol, score, rec, round(close, 2), round(rsi14, 2), round(momentum_20d, 2), round(momentum_60d, 2), round(volume_ratio, 2), round(trend_score, 2), lb, ", ".join(reasons[:5]))


def current_position_symbols(portfolio: Dict[str, Any]) -> set:
    return {p["symbol"] for p in portfolio.get("positions", [])}


def sell_position(portfolio: Dict[str, Any], position: Dict[str, Any], price: float, reason: str) -> None:
    proceeds = position["shares"] * price
    cost = position["shares"] * position["entry_price"]
    pnl = proceeds - cost
    pnl_pct = (price / position["entry_price"] - 1) * 100
    portfolio["cash"] += proceeds
    portfolio["positions"].remove(position)
    portfolio.setdefault("closed_trades", []).append({"symbol": position["symbol"], "entry_date": position["entry_date"], "exit_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"), "entry_price": round(position["entry_price"], 2), "exit_price": round(price, 2), "shares": position["shares"], "pnl": round(pnl, 2), "pnl_pct": round(pnl_pct, 2), "reason": reason})


def update_positions(portfolio: Dict[str, Any], signal_map: Dict[str, Signal]) -> None:
    stop = float(portfolio.get("stop_loss_pct", 0.08))
    target = float(portfolio.get("take_profit_pct", 0.18))
    for position in list(portfolio.get("positions", [])):
        sig = signal_map.get(position["symbol"])
        if not sig:
            continue
        gain_pct = sig.close / position["entry_price"] - 1
        if gain_pct <= -stop:
            sell_position(portfolio, position, sig.close, "stop loss")
        elif gain_pct >= target:
            sell_position(portfolio, position, sig.close, "take profit")
        elif sig.score < 55:
            sell_position(portfolio, position, sig.close, "score breakdown")


def buy_candidates(portfolio: Dict[str, Any], signals: List[Signal]) -> None:
    held = current_position_symbols(portfolio)
    max_positions = int(portfolio.get("max_positions", 8))
    min_score = float(portfolio.get("min_score_to_buy", 72))
    position_size_pct = float(portfolio.get("position_size_pct", 0.12))
    slots = max_positions - len(portfolio.get("positions", []))
    if slots <= 0:
        return
    candidates = [s for s in signals if s.score >= min_score and s.symbol not in held]
    candidates = sorted(candidates, key=lambda s: s.score, reverse=True)
    for sig in candidates[:slots]:
        cash = portfolio["cash"]
        allocation = min(cash, max(0, portfolio["starting_cash"] * position_size_pct))
        shares = int(allocation // sig.close)
        if shares <= 0:
            continue
        portfolio["cash"] -= shares * sig.close
        portfolio.setdefault("positions", []).append({"symbol": sig.symbol, "entry_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"), "entry_price": sig.close, "shares": shares, "entry_score": sig.score})


def portfolio_value(portfolio: Dict[str, Any], signal_map: Dict[str, Signal]) -> Tuple[float, float]:
    invested = sum(p["shares"] * signal_map[p["symbol"]].close for p in portfolio.get("positions", []) if p["symbol"] in signal_map)
    total = portfolio["cash"] + invested
    return round(total, 2), round(invested, 2)


def append_equity_curve(total_value: float) -> None:
    path = DATA_DIR / "equity_curve.csv"
    row = pd.DataFrame([{"date": datetime.now(timezone.utc).strftime("%Y-%m-%d"), "total_value": total_value}])
    if path.exists():
        old = pd.read_csv(path)
        old = old[old["date"] != row["date"].iloc[0]]
        out = pd.concat([old, row], ignore_index=True)
    else:
        out = row
    out.to_csv(path, index=False)


def generate_dashboard(portfolio: Dict[str, Any], signals: List[Signal], signal_map: Dict[str, Signal]) -> None:
    total, invested = portfolio_value(portfolio, signal_map)
    cash = round(portfolio["cash"], 2)
    start = portfolio["starting_cash"]
    return_pct = round((total / start - 1) * 100, 2)
    signals_df = pd.DataFrame([s.__dict__ for s in signals]).sort_values("score", ascending=False)
    signals_df.to_csv(DATA_DIR / "signals.csv", index=False)
    positions_rows = []
    for p in portfolio.get("positions", []):
        sig = signal_map.get(p["symbol"])
        if not sig:
            continue
        current_value = p["shares"] * sig.close
        pnl = current_value - p["shares"] * p["entry_price"]
        pnl_pct = (sig.close / p["entry_price"] - 1) * 100
        positions_rows.append({"symbol": p["symbol"], "shares": p["shares"], "entry": round(p["entry_price"], 2), "current": sig.close, "value": round(current_value, 2), "pnl": round(pnl, 2), "pnl_pct": round(pnl_pct, 2), "score": sig.score})
    positions_df = pd.DataFrame(positions_rows)
    latest = {"date": datetime.now(timezone.utc).isoformat(), "total_value": total, "cash": cash, "invested": invested, "return_pct": return_pct, "top_signals": signals_df.head(10).to_dict(orient="records"), "positions": positions_rows, "closed_trades": portfolio.get("closed_trades", [])[-20:]}
    (DATA_DIR / "latest_run.json").write_text(json.dumps(latest, indent=2), encoding="utf-8")
    append_equity_curve(total)
    figs = []
    eq_path = DATA_DIR / "equity_curve.csv"
    if eq_path.exists():
        eq = pd.read_csv(eq_path, parse_dates=["date"])
        p = figure(title="Paper Portfolio Equity Curve", x_axis_type="datetime", width=950, height=320)
        p.line(eq["date"], eq["total_value"], line_width=3)
        p.circle(eq["date"], eq["total_value"], size=6)
        p.add_tools(HoverTool(tooltips=[("Date", "@x{%F}"), ("Value", "@y{$0,0.00}")], formatters={"@x": "datetime"}))
        figs.append(p)
    charts_html = file_html(column(*figs), CDN, "Charts") if figs else ""
    top_table = signals_df.head(15).to_html(index=False, classes="table", border=0)
    pos_table = positions_df.to_html(index=False, classes="table", border=0) if not positions_df.empty else "<p>No open positions yet.</p>"
    closed_df = pd.DataFrame(portfolio.get("closed_trades", [])[-20:])
    closed_table = closed_df.to_html(index=False, classes="table", border=0) if not closed_df.empty else "<p>No closed trades yet.</p>"
    html = f"""
<!DOCTYPE html><html><head><meta charset="utf-8"><title>LevelUp Swing Trader</title><meta name="viewport" content="width=device-width, initial-scale=1"><style>body{{font-family:Arial,sans-serif;background:#0e1117;color:#f4f4f4;margin:0;padding:24px}}.wrap{{max-width:1200px;margin:auto}}h1,h2{{color:#f4c430}}.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:14px;margin:22px 0}}.card{{background:#171b24;border:1px solid #2f3542;border-radius:12px;padding:18px;box-shadow:0 0 18px rgba(244,196,48,.08)}}.label{{color:#9aa4b2;font-size:13px}}.value{{font-size:28px;font-weight:700;margin-top:6px}}table{{border-collapse:collapse;width:100%;background:#171b24;margin-bottom:26px;font-size:14px}}th,td{{padding:9px;border-bottom:1px solid #2f3542;text-align:right}}th{{background:#222837;color:#f4c430}}td:first-child,th:first-child{{text-align:left}}p{{color:#cbd3df}}.note{{color:#9aa4b2;font-size:13px;margin-top:30px}}</style></head><body><div class="wrap"><h1>LevelUp Swing Trader</h1><p>Fake money. Real market data. Daily swing-trading discipline.</p><div class="cards"><div class="card"><div class="label">Total Value</div><div class="value">${total:,.2f}</div></div><div class="card"><div class="label">Cash</div><div class="value">${cash:,.2f}</div></div><div class="card"><div class="label">Invested</div><div class="value">${invested:,.2f}</div></div><div class="card"><div class="label">Return</div><div class="value">{return_pct}%</div></div></div><h2>Open Positions</h2>{pos_table}<h2>Top Swing Signals</h2>{top_table}<h2>Recent Closed Trades</h2>{closed_table}<h2>Equity Curve</h2>{charts_html}<p class="note">Educational paper-trading dashboard only. This does not place real trades and is not financial advice. Last updated UTC: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}</p></div></body></html>
"""
    (DOCS_DIR / "index.html").write_text(html, encoding="utf-8")


def main() -> None:
    api_key = os.environ.get("ALPHA_VANTAGE_API_KEY")
    if not api_key:
        raise SystemExit("Missing ALPHA_VANTAGE_API_KEY. Add it as a GitHub repository secret.")
    portfolio = load_portfolio()
    signals: List[Signal] = []
    for idx, symbol in enumerate(portfolio.get("symbols", []), start=1):
        print(f"[{idx}/{len(portfolio.get('symbols', []))}] Scanning {symbol}")
        df = get_price_data(symbol, api_key)
        sig = score_symbol(symbol, df, portfolio.get("closed_trades", []))
        signals.append(sig)
        time.sleep(13)
    signal_map = {s.symbol: s for s in signals}
    update_positions(portfolio, signal_map)
    buy_candidates(portfolio, sorted(signals, key=lambda x: x.score, reverse=True))
    save_portfolio(portfolio)
    generate_dashboard(portfolio, signals, signal_map)
    print("Done. Dashboard written to docs/index.html")


if __name__ == "__main__":
    main()
