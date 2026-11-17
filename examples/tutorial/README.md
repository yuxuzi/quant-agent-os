# Tutorial files

The files [docs/tutorial.md](../../docs/tutorial.md) uses. All the data is synthetic.

| File | Used in | What it is |
| --- | --- | --- |
| `env.sh` | Setup, and each new terminal | Tutorial settings (`$REPO`, `$TUT`), with everything in `~/qaos-tutorial`, and the `call` and `get` helpers. Source it from any folder |
| `data/fund_returns.csv` | Labs 2, 5, 7 | 36 months of a fund and its benchmark |
| `data/portfolio_returns.csv` | Labs 2, 7 | 36 months of three assets and two factors |
| `lab_analytics.py` | Lab 2 | Performance and risk metrics by dataset id |
| `show.py` | Labs 5 to 7 | Prints REST responses as a few readable lines |
| `lab_guardrails.py` | Lab 8 | Refusals, and the approval flow |
| `data/notes/my-reversal-note.md` | Lab 9 | A note to add to the knowledge store |
