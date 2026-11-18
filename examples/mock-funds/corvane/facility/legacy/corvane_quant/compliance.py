import config


def check(account, weights, master):
    bad = []
    for sec, w in weights.items():
        if w > 0 and master.loc[sec == master.SECURITY_ID, "INDUSTRY"].iloc[
            0
        ] in config.BANNED_INDUSTRIES.get(account, []):
            bad.append((sec, w))
        if (
            account in config.MAX_POSITION
            and sec != "CASH_USD"
            and w > config.MAX_POSITION[account]
        ):
            bad.append((sec, w))
    if bad:
        print("BREACH", account, bad)
    return bad
