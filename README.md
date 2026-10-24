# Multi-strategy paper bot (KuCoin Futures)

Paper trading only: public KuCoin data, **no API keys, no real orders**.

## What it does
Two systematic books, the way quant funds combine strategies, across the ~50 most traded KuCoin USDT perpetuals:
- **CTA trend (30%):** each coin long or short by three EMA crossovers, sized by 1/volatility.
- **Volatility-managed momentum (70%):** long the 14-day winners, short the losers; exposure is cut when momentum
  itself becomes volatile (Barroso & Santa-Clara), which avoids most momentum crashes.

The whole portfolio aims at 40% yearly volatility (max 3x gross). Books rebalance daily at 12:00 UTC; the
volatility scaling adjusts every 4 hours. Funding is charged/credited from KuCoin's current rates.

## Backtest it came from (run 2026-10-24e, q07_cta_plus_vmm; 42 coins, fees 0.06% + 2 bps)
| Period | Return | Sharpe | Max DD |
|---|---|---|---|
| Train, Oct 2024 – Jan 2026 (bull) | +141% | 1.89 | −28% |
| Validation, Jan – May 2026 (bear) | +15% (+11.9% with real funding) | 1.04 (0.90) | −22% |
| Locked final test, Jun – Oct 2026 | +56% | 2.65 | −17% |

All 9 mix settings tried were profitable on train; ±20% settings and double costs stay profitable on validation.
It narrowly missed one gate (daily profit factor 1.16 vs 1.2) and was still given the final test, which it passed.

**Weak points:** a few coins drive much of the profit (without IRYS the final test is +34.5%; without IRYS,
USELESS and NIGHT it is +12%); the coin list is today's top coins (survivorship bias); part of the final test
overlapped an earlier momentum test. Expect lumpy returns and drawdowns of 20–30%.

**Checked:** the bot's weights match the research code (difference < 0.1% with 120+ days of history), and replaying
the bot over the final-test period gave +59.7% vs the backtest's +55.5%.

## Render
Background worker, Starter plan, Frankfurt, Python 3.12.8, disk at `/data` (all in `render.yaml`). The first
start downloads 150 days of hourly candles for ~50 coins (a few minutes); they are cached on the disk.

## Settings
| Variable | Default | Meaning |
|---|---|---|
| `MODE` | `paper` | only paper is supported |
| `UNIVERSE_SIZE` / `PAIRS` / `EXCLUDE` | `50` / empty / stock tokens | coins traded |
| `W_CTA` | `0.3` | share of the trend book (rest is momentum) |
| `TARGET_VOL` | `0.4` | yearly volatility target of the whole book |
| `MAX_GROSS` | `3.0` | cap on total long + short exposure / equity |
| `FEE` / `SLIPPAGE` | `0.0006` / `0.0002` | per side, as in the backtest |
| `MIN_TRADE_USDT` | `5` | smaller adjustments skipped |
| `START_CASH` | `1000` | paper balance |
| `HISTORY_DAYS` | `150` | candle history used |
| `DATA_DIR` | `.` | state, trades, equity and the candle cache |

`python bot.py --report` shows results. Kill switch: a file named `STOP`.
