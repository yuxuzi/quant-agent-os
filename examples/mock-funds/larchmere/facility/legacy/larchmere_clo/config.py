import os

DB_PATH = os.environ.get("LARCH_DB", "clo.db")
OUTPUT_DIR = os.environ.get("LARCH_OUT", "/tmp")
TRUSTEE_TOLERANCE = 0.0005
CCC_LIMIT = 0.075
RF = {
    "Ba1": 940,
    "Ba2": 1350,
    "Ba3": 1766,
    "B1": 2220,
    "B2": 2720,
    "B3": 3490,
    "Caa1": 4770,
    "Caa2": 6500,
    "Caa3": 8070,
    "Ca": 10000,
    "D": 10000,
}
SENIOR = {
    "oc_ab": ["AAA", "AA"],
    "oc_c": ["AAA", "AA", "A"],
    "oc_d": ["AAA", "AA", "A", "BBB"],
    "oc_e": ["AAA", "AA", "A", "BBB", "BB"],
}
