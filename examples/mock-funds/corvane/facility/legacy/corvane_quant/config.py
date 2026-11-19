import os

import pandas as pd

LAKE = os.environ.get("CORVANE_LAKE", "s3/corvane-curated/curated")
REPORT_DATE = "2026-05-15"
BENCH = {"LCC": "BM_US300", "GQ": "BM_GLOBAL200"}
MAX_POSITION = {"SMA_PEN_A": 0.07, "SMA_SWF_C": 0.05}
BANNED_INDUSTRIES = {"SMA_FND_B": ["Tobacco"], "SMA_SWF_C": ["Tobacco"]}
SECTOR_MAP = None  # filled by holdings.load_master()
PRICES = None  # the price cache everyone shares; see holdings.load_prices()
pd.set_option("display.width", 200)
