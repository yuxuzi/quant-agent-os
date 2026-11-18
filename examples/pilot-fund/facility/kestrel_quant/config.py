"""Global settings. Edit here, or override in your script before calling anything."""

import os

CONFIG = {
    "warehouse": os.environ.get("KPC_WAREHOUSE", "warehouse/kestrel.db"),
    "output_dir": os.environ.get("KPC_OUTPUT", "."),
    "verbose": True,
    "annualization": 252,
    "momentum": {"lookback": 126, "skip": 21},
    "carry": {"winsor": 3.0},
    "value": {"by": "region"},
    "vol_target": {"target": 0.10, "window": 63, "max_leverage": 3.0},
    "desks": ["eq_ls", "macro", "commod"],
    "risk": {
        "lookback": 500,
        "confidence": 0.99,
        "load_delay_s": 1.0,  # the vendor model download, as it is in production
    },
}


def log(*args):
    if CONFIG["verbose"]:
        print("[kestrel_quant]", *args)
