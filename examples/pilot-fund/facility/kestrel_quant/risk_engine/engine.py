"""The risk engine. Holds the covariance, the vendor model, and the desks' books in memory."""

import json
import os
import time

import numpy as np
import pandas as pd

from kestrel_quant import db
from kestrel_quant.config import CONFIG, log

SCENARIO_FILE = os.path.join(os.path.dirname(__file__), "scenarios.json")
Z = {0.95: 1.6448536269514722, 0.975: 1.959963984540054, 0.99: 2.3263478740408408}


class RiskEngineError(Exception):
    pass


class RiskEngine:
    def __init__(self, as_of=None, lookback=None, scenarios=None, **opts):
        self.as_of = as_of
        self.lookback = lookback or CONFIG["risk"]["lookback"]
        self.scenario_file = scenarios or SCENARIO_FILE
        self.opts = opts
        self.loaded = False
        self._cache = {}
        self.returns = None
        self.cov = None
        self.loadings = None
        self.factor_cov = None
        self.specific = None
        self.books = {}
        self.scenarios = {}
        self.load_seconds = None

    # ---- loading -------------------------------------------------------------------------

    def load(self):
        t0 = time.time()
        log("risk engine: loading as of", self.as_of or "latest")
        rets = db.load_returns()
        if self.as_of:
            rets = rets.loc[: self.as_of]
        if rets.empty:
            raise RiskEngineError(f"no returns on or before {self.as_of}")
        self.as_of = rets.index[-1].strftime("%Y-%m-%d")
        self.returns = rets.iloc[-self.lookback :]
        self.cov = self._ewma_cov(self.returns, self.opts.get("halflife", 60))
        self._load_vendor_model()
        self._load_books()
        with open(self.scenario_file) as f:
            self.scenarios = {k: v for k, v in json.load(f).items() if not k.startswith("_")}
        delay = self.opts.get("load_delay_s", CONFIG["risk"]["load_delay_s"])
        if delay:
            time.sleep(delay)  # the vendor download in production
        self._cache.clear()
        self.loaded = True
        self.load_seconds = round(time.time() - t0, 3)
        log("risk engine: loaded", len(self.books), "desks in", self.load_seconds, "s")
        return self

    def refresh(self, as_of=None):
        db.clear_cache()
        self.as_of = as_of
        return self.load()

    @staticmethod
    def _ewma_cov(rets, halflife):
        lam = 0.5 ** (1.0 / halflife)
        x = rets.fillna(0.0).values
        x = x - x.mean(axis=0)
        w = lam ** np.arange(len(x))[::-1]
        w = w / w.sum()
        cov = (x * w[:, None]).T @ x
        return pd.DataFrame(cov, index=rets.columns, columns=rets.columns)

    def _load_vendor_model(self):
        dates = db.query("SELECT DISTINCT as_of_date FROM fm_loadings ORDER BY as_of_date")
        usable = dates[dates.as_of_date <= self.as_of].as_of_date
        if usable.empty:
            raise RiskEngineError(f"no vendor model on or before {self.as_of}")
        model_date = usable.iloc[-1]
        lo = db.query(
            "SELECT instrument_id, factor_id, loading FROM fm_loadings WHERE as_of_date = ?",
            model_date,
        )
        self.loadings = lo.pivot(index="instrument_id", columns="factor_id", values="loading")
        self.loadings = self.loadings.fillna(0.0)
        fc = db.query(
            "SELECT factor_id_1, factor_id_2, cov_annual FROM fm_factor_cov WHERE as_of_date = ?",
            model_date,
        )
        self.factor_cov = fc.pivot(index="factor_id_1", columns="factor_id_2", values="cov_annual")
        self.factor_cov = self.factor_cov.loc[self.loadings.columns, self.loadings.columns]
        sp = db.query(
            "SELECT instrument_id, specific_var_annual FROM fm_specific WHERE as_of_date = ?",
            model_date,
        )
        self.specific = sp.set_index("instrument_id").specific_var_annual
        self.model_date = model_date

    def _load_books(self):
        pos = db.query(
            "SELECT desk, instrument_id, market_value FROM pos_eod WHERE date = ?", self.as_of
        )
        self.books = {
            desk: grp.set_index("instrument_id").market_value for desk, grp in pos.groupby("desk")
        }

    # ---- questions -----------------------------------------------------------------------

    def _need(self, desk):
        if not self.loaded:
            raise RiskEngineError("call load() first")
        if desk not in self.books:
            raise RiskEngineError(f"unknown desk {desk!r}; known: {sorted(self.books)}")
        return self.books[desk]

    def desks(self):
        return sorted(self.books)

    def exposure(self, desk):
        mv = self._need(desk)
        return {"gross": float(mv.abs().sum()), "net": float(mv.sum()), "positions": len(mv)}

    def desk_var(self, desk, confidence=None, horizon=1, method="historical"):
        """Value at risk in USD, a positive number: the loss not exceeded with ``confidence``."""
        confidence = confidence or CONFIG["risk"]["confidence"]
        key = ("var", desk, confidence, horizon, method)
        if key in self._cache:
            return self._cache[key]
        mv = self._need(desk)
        if method == "historical":
            pnl = self.returns[mv.index].fillna(0.0) @ mv
            var = -np.quantile(pnl, 1 - confidence) * np.sqrt(horizon)
        elif method == "parametric":
            if confidence not in Z:
                raise RiskEngineError(f"parametric VaR supports {sorted(Z)}")
            cov = self.cov.loc[mv.index, mv.index].values
            sigma = float(np.sqrt(mv.values @ cov @ mv.values))
            var = Z[confidence] * sigma * np.sqrt(horizon)
        elif method == "factor":
            var = Z[confidence] * self._model_sigma(mv) * np.sqrt(horizon)
        else:
            raise RiskEngineError(f"unknown method {method!r}")
        out = {
            "desk": desk,
            "as_of": self.as_of,
            "method": method,
            "confidence": confidence,
            "horizon_days": horizon,
            "var_usd": float(var),
            "var_pct_gross": float(var / mv.abs().sum()),
        }
        self._cache[key] = out
        return out

    def _model_sigma(self, mv):
        """Daily P&L sigma in USD from the vendor model."""
        b = self.loadings.reindex(mv.index).fillna(0.0)
        x = b.T.values @ mv.values
        ann = CONFIG["annualization"]
        factor_var = x @ self.factor_cov.values @ x / ann
        spec_var = float((mv**2 * self.specific.reindex(mv.index).fillna(0.0)).sum()) / ann
        return float(np.sqrt(factor_var + spec_var))

    def stress(self, desk, scenario):
        mv = self._need(desk)
        if scenario not in self.scenarios:
            raise RiskEngineError(f"unknown scenario {scenario!r}; known: {sorted(self.scenarios)}")
        spec = self.scenarios[scenario]
        shocks = pd.Series(spec.get("factors", {})).reindex(self.loadings.columns).fillna(0.0)
        b = self.loadings.reindex(mv.index).fillna(0.0)
        move = b @ shocks
        classes = db.instruments().set_index("instrument_id").asset_class
        for asset_class, extra in spec.get("asset_classes", {}).items():
            move = move + (classes.reindex(mv.index) == asset_class) * extra
        pnl = mv * move
        worst = pnl.sort_values().head(3)
        return {
            "desk": desk,
            "scenario": scenario,
            "description": spec.get("description", ""),
            "pnl_usd": float(pnl.sum()),
            "worst_positions": {k: float(v) for k, v in worst.items()},
        }

    def stress_all(self, desk):
        return [self.stress(desk, name) for name in sorted(self.scenarios)]

    def factor_decomposition(self, desk):
        """Each factor's share of the desk's model variance, and the specific share."""
        key = ("decomp", desk)
        if key in self._cache:
            return self._cache[key]
        mv = self._need(desk)
        b = self.loadings.reindex(mv.index).fillna(0.0)
        x = b.T.values @ mv.values
        fx = self.factor_cov.values @ x
        contrib = x * fx  # sums to the factor variance
        spec = float((mv**2 * self.specific.reindex(mv.index).fillna(0.0)).sum())
        total = float(contrib.sum() + spec)
        out = {
            "desk": desk,
            "as_of": self.as_of,
            "model_date": self.model_date,
            "vol_annual_usd": float(np.sqrt(total)),
            "exposures_usd": dict(zip(b.columns, map(float, x), strict=True)),
            "variance_share": {
                f: float(c / total) for f, c in zip(b.columns, contrib, strict=True)
            },
            "specific_share": spec / total,
        }
        self._cache[key] = out
        return out
