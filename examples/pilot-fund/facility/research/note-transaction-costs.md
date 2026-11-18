---
title: Research note - transaction cost model
authors: Execution and research, Kestrel Point Capital (fictional)
published: 2024-11-05
license: CC0 synthetic fixture
---

# Transaction cost model

We charge one basis point of notional as commission on every trade, plus a market impact term that grows
with the square root of the trade's share of average daily value (ADV). Equities have the lowest ADV in
the warehouse, tens to hundreds of millions of dollars a day; G10 FX the highest, tens of billions.

Rules of thumb for backtests:

- Report turnover, the cost per unit of turnover, and the annual cost drag.
- Trade on the next day's close after a signal, never the same close.
- A position larger than 10% of an instrument's ADV needs execution's sign-off.
