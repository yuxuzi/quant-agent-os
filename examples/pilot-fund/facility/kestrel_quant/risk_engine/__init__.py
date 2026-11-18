"""Desk risk: VaR, stress, and factor decomposition. Load once, ask many times.

eng = RiskEngine()
eng.load()            # slow: reads the warehouse and the vendor model
eng.desk_var("macro")
"""

from kestrel_quant.risk_engine.engine import RiskEngine, RiskEngineError  # noqa: F401
