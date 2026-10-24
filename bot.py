"""Multi-strategy paper bot for KuCoin Futures (public data only, no API keys, no real orders).

Two quant books in one portfolio across the ~50 most traded USDT perpetuals:
  * CTA trend: three EMA crossovers per coin, sized by 1/volatility, long and short
  * volatility-managed momentum: long the 14-day winners, short the losers, exposure cut when momentum gets wild
mixed 30/70 and scaled so the whole portfolio aims at 40% yearly volatility (max 3x gross). The books rebalance
daily at 12:00 UTC; the volatility scaling adjusts every 4 hours. Funding is charged or credited.

    python bot.py            # runs forever (acts every 4h at 00/04/08/12/16/20 UTC)
    python bot.py --once     # one decision now, then exits
    python bot.py --report   # paper results so far
    touch STOP               # kill switch
"""
import csv
import json
import os
import sys
import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd

import kucoin_data as kd
from q07_cta_plus_vmm import signal

def _f(name, default):
    return float(os.getenv(name, default))


MODE = os.getenv("MODE", "paper").lower()
UNIVERSE_SIZE = int(os.getenv("UNIVERSE_SIZE", "50"))
PAIRS = [p.strip().upper() for p in os.getenv("PAIRS", "").split(",") if p.strip()]
EXCLUDE = {s.strip().upper() for s in os.getenv(
    "EXCLUDE", "TSLA,SOXL,SPCX,SNDK,MU,PAXG,XAUT,NVDA,AAPL,AMZN,GOOGL,META,MSFT,COIN,MSTR,HOOD,CRCL,QQQ,SPY").split(",")}
W_CTA = _f("W_CTA", "0.3")                     # share of the CTA trend book (rest: vol-managed momentum)
TARGET_VOL = _f("TARGET_VOL", "0.4")          # yearly volatility the whole book aims for
MAX_GROSS = _f("MAX_GROSS", "3.0")
FEE = _f("FEE", "0.0006")
SLIPPAGE = _f("SLIPPAGE", "0.0002")            # backtest assumption: 2 bps
MIN_TRADE_USDT = _f("MIN_TRADE_USDT", "5")
START_CASH = _f("START_CASH", "1000")
FUNDING = os.getenv("FUNDING", "true").lower() in ("1", "true", "yes")
DATA_DIR = os.getenv("DATA_DIR", ".")
CACHE_DIR = os.path.join(DATA_DIR, "candles")
STATE_PATH = os.path.join(DATA_DIR, os.getenv("STATE_FILE", "multistrat_state.json"))
EQUITY_CSV = os.path.join(DATA_DIR, "multistrat_equity.csv")
TRADES_CSV = os.path.join(DATA_DIR, "multistrat_trades.csv")
HISTORY_H = int(os.getenv("HISTORY_DAYS", "150")) * 24
H = 3600


def log(msg):
    print(f"[{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S}Z] {msg}", flush=True)


def append_csv(path, header, row):
    new = not os.path.exists(path)
    with open(path, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(header)
        w.writerow(row)


def load_state():
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH) as f:
            return json.load(f)
    return {"cash": START_CASH, "pos": {}, "fees": 0.0, "funding": 0.0, "last_bar": None, "decisions": 0,
            "universe": [], "universe_at": 0, "started": time.time()}


def save_state(st):
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f, indent=1)
    os.replace(tmp, STATE_PATH)


def equity(st, prices):
    return st["cash"] + sum(p["qty"] * (prices.get(s, p["entry"]) - p["entry"]) for s, p in st["pos"].items())


def universe(st, now):
    if PAIRS:
        return [p if p.endswith("USDTM") else ("XBT" if p == "BTC" else p) + "USDTM" for p in PAIRS]
    if st["universe"] and now - st["universe_at"] < 86400:
        return st["universe"]
    rows = [r for r in kd.active_contracts() if r["base"].upper() not in EXCLUDE and r["symbol"].replace("USDTM", "") not in EXCLUDE]
    rows.sort(key=lambda r: -r["turnover"])
    syms = [r["symbol"] for r in rows[:UNIVERSE_SIZE]]
    if "XBTUSDTM" not in syms:
        syms.append("XBTUSDTM")                      # BTC is always needed
    st["universe"], st["universe_at"] = syms, now
    return syms


class Candles:
    """1h candles per coin, kept on disk so a restart doesn't refetch 150 days of 50 coins."""

    def __init__(self, fetch=None):
        self.fetch = fetch or (lambda s, n, end: kd.candles_1h(s, n, end_ts=end))
        os.makedirs(CACHE_DIR, exist_ok=True)
        self.mem = {}

    def get(self, sym, now):
        df = self.mem.get(sym)
        path = os.path.join(CACHE_DIR, f"{sym}.csv")
        if df is None and os.path.exists(path):
            df = pd.read_csv(path)
        want_last = (int(now) // H) * H - H
        last = int(df.t_ms.iloc[-1] // 1000) if df is not None and len(df) else 0
        if df is None or last < want_last - 150 * H:
            df = self.fetch(sym, HISTORY_H, now)
        elif last < want_last:
            new = self.fetch(sym, min(HISTORY_H, (want_last - last) // H + 6), now)
            df = pd.concat([df, new]).drop_duplicates("t_ms").sort_values("t_ms")
        df = df.tail(HISTORY_H).reset_index(drop=True)
        self.mem[sym] = df
        df.to_csv(path, index=False)
        return df


def four_hour_panel(frames):
    """{sym: 1h candles} -> 4h closes (close time of the 1h candle ending at 00/04/.../20 UTC), as in the backtest."""
    cols = {}
    for s, df in frames.items():
        close_t = df.t_ms.values + H * 1000
        keep = close_t % (4 * H * 1000) == 0
        cols[s] = pd.Series(df.close.values[keep], index=pd.to_datetime(close_t[keep], unit="ms", utc=True))
    P = pd.DataFrame(cols).sort_index()
    return P, P.pct_change(fill_method=None)


def decide(st, candles, now=None):
    now = now or time.time()
    bar = int(now // (4 * H)) * 4 * H                       # close time of the newest finished 4h bar
    if st["last_bar"] == bar:
        return False
    syms = universe(st, now)
    frames, prices = {}, {}
    for s in sorted(set(syms) | set(st["pos"])):
        try:
            frames[s] = candles.get(s, now)
            prices[s] = float(frames[s].close.iloc[-1])
        except Exception as e:
            log(f"{s}: data error ({e})")
    if "XBTUSDTM" not in frames:
        log("no BTC data - decision skipped")
        return False
    P, R = four_hour_panel({s: frames[s] for s in syms if s in frames})
    W = signal(P, R, None, w_cta=W_CTA, tvol=TARGET_VOL)
    w = W.iloc[-1].fillna(0.0)
    g = w.abs().sum()
    if g > MAX_GROSS:
        w = w * MAX_GROSS / g
    # funding for the 4h that passed (longs pay a positive rate)
    if FUNDING and st["pos"] and st["last_bar"]:
        hours = min((bar - st["last_bar"]) / H, 12)
        for s, p in st["pos"].items():
            pay = p["qty"] * prices.get(s, p["entry"]) * kd.funding_rate(s) * hours / 8.0
            st["cash"] -= pay
            st["funding"] -= pay
    eq = equity(st, prices)
    for s, p in st["pos"].items():                         # realise P&L, then trade to the new targets
        px = prices.get(s, p["entry"])
        st["cash"] += p["qty"] * (px - p["entry"])
        p["entry"] = px
    now_iso = datetime.fromtimestamp(now, timezone.utc).isoformat(timespec="seconds")
    turnover = fees = 0.0
    for s in sorted(set(w.index[w != 0]) | set(st["pos"])):
        if s not in prices:
            continue
        px = prices[s]
        cur = st["pos"].get(s, {}).get("qty", 0.0)
        tgt = float(w.get(s, 0.0)) * eq / px
        dq = tgt - cur
        if abs(dq) * px < MIN_TRADE_USDT and abs(tgt) * px >= MIN_TRADE_USDT:
            continue
        cost = abs(dq) * px * (FEE + SLIPPAGE)
        st["cash"] -= cost
        fees += cost
        turnover += abs(dq) * px
        if abs(tgt) * px < MIN_TRADE_USDT:
            st["pos"].pop(s, None)
        else:
            st["pos"][s] = {"qty": tgt, "entry": px}
        if abs(dq) * px >= MIN_TRADE_USDT:
            append_csv(TRADES_CSV, ["time", "symbol", "qty_change", "price", "new_weight", "cost"],
                       [now_iso, s, round(dq, 8), px, round(float(w.get(s, 0.0)), 4), round(cost, 4)])
    st["fees"] += fees
    st["last_bar"] = bar
    st["decisions"] += 1
    gross = sum(abs(p["qty"]) * prices.get(s, p["entry"]) for s, p in st["pos"].items())
    net = sum(p["qty"] * prices.get(s, p["entry"]) for s, p in st["pos"].items())
    eq = equity(st, prices)
    bar_dt = datetime.fromtimestamp(bar, timezone.utc)
    kind = "daily rebalance" if bar_dt.hour == 12 else "volatility adjustment"
    log(f"{kind} | equity {eq:.2f} ({eq / START_CASH - 1:+.2%}) | {len(st['pos'])} positions | gross {gross / max(eq, 1e-9):.2f}x "
        f"| net {net / max(eq, 1e-9):+.2f}x | traded {turnover:.0f} USDT, cost {fees:.2f} | funding {st['funding']:+.2f}")
    if bar_dt.hour == 12:
        top = w.sort_values()
        log("  biggest longs : " + ", ".join(f"{s.replace('USDTM', '')} {v:+.1%}" for s, v in top[::-1][:6].items() if v > 0))
        log("  biggest shorts: " + ", ".join(f"{s.replace('USDTM', '')} {v:+.1%}" for s, v in top[:6].items() if v < 0))
    append_csv(EQUITY_CSV, ["time", "equity", "positions", "gross", "net", "fees", "funding"],
               [now_iso, round(eq, 4), len(st["pos"]), round(gross, 2), round(net, 2), round(st["fees"], 4), round(st["funding"], 4)])
    save_state(st)
    return True


def report():
    if not os.path.exists(EQUITY_CSV):
        print("no results yet")
        return
    rows = list(csv.DictReader(open(EQUITY_CSV)))
    eqs = [float(r["equity"]) for r in rows]
    peak, dd = eqs[0], 0.0
    for e in eqs:
        peak = max(peak, e)
        dd = min(dd, e / peak - 1)
    print(f"from {rows[0]['time']} to {rows[-1]['time']}: equity {eqs[-1]:.2f} ({eqs[-1] / START_CASH - 1:+.2%}) | max drawdown {dd:.1%} "
          f"| fees {rows[-1]['fees']} | funding {rows[-1]['funding']} | decisions {len(rows)}")


def main():
    if "--report" in sys.argv:
        return report()
    if MODE != "paper":
        raise SystemExit("This bot is paper-only for now (MODE=paper). It never places real orders.")
    log(f"MULTI-STRATEGY PAPER BOT | CTA trend {W_CTA:.0%} + vol-managed momentum {1 - W_CTA:.0%} | target vol {TARGET_VOL:.0%} "
        f"| max gross {MAX_GROSS:g}x | universe {'fixed ' + str(len(PAIRS)) if PAIRS else 'top ' + str(UNIVERSE_SIZE)}")
    st, candles = load_state(), Candles()
    while True:
        if os.path.exists("STOP"):
            log("STOP file found, exiting")
            break
        try:
            decide(st, candles)
        except Exception as e:
            log(f"cycle error: {e}")
        if "--once" in sys.argv:
            break
        time.sleep(H - time.time() % H + 90)


if __name__ == "__main__":
    main()
