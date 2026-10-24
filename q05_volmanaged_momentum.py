"""Volatility-managed momentum (Barroso & Santa-Clara): cross-sectional `k`-day momentum book, scaled each day so its
own recent volatility matches `tvol` - cutting exposure before momentum crashes. (Momentum's holdout was used once.)"""
import numpy as np, pandas as pd
from qcommon import BPD, hold_every, seasoned, neutral, cz, vol_target
def signal(P, R, V, k=7, tvol=0.3, every=6):
    vol = R.rolling(7 * BPD, min_periods=2 * BPD).std()
    sc = (P.pct_change(k * BPD, fill_method=None) / vol).where(seasoned(P))
    W = hold_every(neutral(sc), every)
    return vol_target(W, R, tvol)
