import numpy as np, pandas as pd
BPD = 6                                           # 4h bars per day
ANCHOR = pd.Timestamp("2024-10-24 12:00", tz="UTC")    # the backtest's first 4h bar: rebalances fall on 12:00 UTC


def hold_every(W, every):
    """Rebalance only on bars a whole number of `every` 4h bars after ANCHOR (independent of where the data starts)."""
    k = ((W.index - ANCHOR) // pd.Timedelta(hours=4)).astype("int64")
    keep = pd.Series(np.asarray(k) % every == 0, index=W.index)
    return W.where(keep, np.nan).ffill().fillna(0.0)
def neutral(z):
    z = z.sub(z.mean(axis=1), axis=0); return z.div(z.abs().sum(axis=1), axis=0).fillna(0.0)
def cz(x):
    return x.sub(x.mean(axis=1), axis=0).div(x.std(axis=1), axis=0)
def seasoned(P, days=30):
    return P.notna().cumsum() >= days * BPD
def beta_hedge(W, R, days=30):
    """Add a BTC position that cancels the book's estimated BTC beta (rolling `days` regression, past data only)."""
    b = R["XBTUSDTM"]; n = days * BPD
    beta = R.rolling(n, min_periods=n // 2).cov(b).div(b.rolling(n, min_periods=n // 2).var(), axis=0)
    W = W.copy(); W["XBTUSDTM"] = W.get("XBTUSDTM", 0.0) - (W.drop(columns="XBTUSDTM") * beta.drop(columns="XBTUSDTM")).sum(axis=1)
    return W.fillna(0.0)
def vol_target(W, R, target, days=30):
    """Scale the whole book so its estimated volatility (from the last `days` of the book's own past returns) hits `target` per year."""
    raw = (W.shift(1) * R).sum(axis=1)                                   # what the unscaled book earned each bar
    vol = raw.rolling(days * BPD, min_periods=10 * BPD).std() * np.sqrt(365 * BPD)
    k = (target / vol).clip(upper=3.0).shift(1)                          # use only volatility known before the bar
    return W.mul(k.fillna(0.0), axis=0)
