"""A signal over a dates-by-names panel, read from the warehouse when none is given."""

from firm_legacy import store


def momentum(panel=None, lookback=5):
    """Each name's return over the lookback, z-scored across names on each date."""
    if panel is None:
        panel = store._panel()  # needs the fund's warehouse, which a test does not have
    print("momentum over", len(panel.columns), "names")
    raw = panel.pct_change(lookback)
    return raw.sub(raw.mean(axis=1), axis=0).div(raw.std(axis=1), axis=0)
