"""Global settings. Edit here, or override in your notebook before calling anything."""

import os

CONFIG = {
    # where the curated Delta tables are mounted on the analysts' VMs (a DBFS-style mount)
    "lake": os.environ.get("SAB_LAKE", "/mnt/uc/sablecroft"),
    "verbose": True,
    "periods_per_year": 12,
    "risk_free": 0.0,
    "refresh_delay_s": 1.0,  # the administrator statements parse, as it is in production
}


def log(*args):
    if CONFIG["verbose"]:
        print("[sablecroft_alts]", *args)
