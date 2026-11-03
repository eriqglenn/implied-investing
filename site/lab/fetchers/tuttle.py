"""Tuttle Capital Income Blast reader (incomeblastetfs.com).

The Income Blast fund pages load from the same public fund API as TappAlpha's, so the reader lives in tappalpha.py.
"""
from .tappalpha import RATES, holdings, rate  # noqa: F401
