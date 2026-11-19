import sqlite3

import pandas as pd
from config import DB_PATH

conn = None


def get_conn():
    global conn
    if conn is None:
        conn = sqlite3.connect(DB_PATH)
        print("connected to", DB_PATH)
    return conn


def q(sql, params=None):
    return pd.read_sql_query(sql, get_conn(), params=params)
