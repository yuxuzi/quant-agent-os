import math
import time

from wrenfield_quant import CONFIG

# Slow and stateful: bootstraps a hazard-rate curve per name from the CDS quotes and keeps them
# in a module-level dict, so the first call after a restart takes minutes on the real desk.
CURVES = {}
LOADED = False


def load_curves(delay=2.0):
    global LOADED
    from wrenfield_quant import basis

    time.sleep(delay)
    for tenor, years in (("3Y", 3.0), ("5Y", 5.0), ("10Y", 10.0)):
        quotes = basis._cds(tenor)
        for name, spread in quotes.items():
            CURVES.setdefault(name, {})[years] = spread
    LOADED = True
    print("loaded", len(CURVES), "curves")


def survival(name, years):
    if not LOADED:
        raise RuntimeError("call load_curves() first")
    quotes = CURVES[name]
    spread = quotes[min(quotes, key=lambda t: abs(t - years))]
    hazard = spread / 1e4 / (1.0 - CONFIG["recovery"])
    return math.exp(-hazard * years)


def jtd_cds(name, notional):
    if not LOADED:
        raise RuntimeError("call load_curves() first")
    return notional * (1.0 - CONFIG["recovery"])
