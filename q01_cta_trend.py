"""CTA / managed-futures trend: per coin, average the sign of three EMA crossovers (fast/slow pairs scaled by `mult`),
size each coin by 1/volatility, then scale the book to `tvol` annual volatility. Long and short; daily rebalancing."""
import numpy as np, pandas as pd
from qcommon import BPD, hold_every, seasoned, vol_target
def signal(P, R, V, mult=1.0, tvol=0.2, every=6):
    s = 0
    for f, sl in ((8, 32), (16, 64), (32, 128)):
        f, sl = int(f * mult * BPD / 2), int(sl * mult * BPD / 2)
        s = s + np.sign(P.ewm(span=f).mean() - P.ewm(span=sl).mean())
    s = s / 3
    vol = R.rolling(30 * BPD, min_periods=10 * BPD).std()
    W = (s / vol).where(seasoned(P)); W = W.div(W.abs().sum(axis=1), axis=0)
    return vol_target(hold_every(W.fillna(0.0), every), R, tvol)
