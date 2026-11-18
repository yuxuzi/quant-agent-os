"""A stateful risk model with a slow start and a cache: served as a service."""

import math
import time


class RiskModel:
    """A toy covariance-free risk model that takes a while to load."""

    def __init__(self, halflife=60):
        time.sleep(0.2)  # stands for loading a large model from disk
        self.halflife = halflife
        self._cache = {}

    def portfolio_var(self, weights, confidence=0.99):
        """One-day value at risk of a portfolio, as a fraction of its value."""
        key = (tuple(weights), confidence)
        if key not in self._cache:
            spread = math.sqrt(sum(weight * weight for weight in weights)) * 0.01
            self._cache[key] = round(spread * (2.33 if confidence >= 0.99 else 1.65), 10)
        return self._cache[key]
