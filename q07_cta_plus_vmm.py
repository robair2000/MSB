"""Two-strategy portfolio (multi-strat): the CTA trend book (q01, train-chosen mult 0.5) and the volatility-managed
momentum book (q05, train-chosen 14-day lookback), each scaled to the same risk, mixed `w_cta` : 1-w_cta, and the sum
re-scaled to `tvol`. Hypothesis: two lowly correlated books that each made money in both regimes add up to a better
risk-adjusted return than either alone."""
import numpy as np, pandas as pd
from qcommon import vol_target
import q01_cta_trend as A, q05_volmanaged_momentum as B
def signal(P, R, V, w_cta=0.5, tvol=0.25):
    a = A.signal(P, R, V, mult=0.5, tvol=0.2, every=6); b = B.signal(P, R, V, k=14, tvol=0.2, every=6)
    W = w_cta * a.reindex(columns=P.columns, fill_value=0) + (1 - w_cta) * b.reindex(columns=P.columns, fill_value=0)
    return vol_target(W.fillna(0.0), R, tvol)
