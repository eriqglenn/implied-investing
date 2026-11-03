"""Issuer readers for the Income Lab refresh (see site/lab/refresh.py).

Each module is named after the issuer key in state.json and exposes:

  RATES = True | False
      True if the issuer publishes distribution rates somewhere this module can read reliably.

  def holdings(fund, http) -> dict
      fund: the fund's dict from state.json (keys t, name, official_name, url, cusip, ...)
      http: refresh.Http (http.get(url, headers=..., params=...) -> requests.Response; raises on failure)
      returns {"as_of": "YYYY-MM-DD", "source_url": str, "total": int|None,
               "top": [[name, ticker|None, weight_pct (number, signed), type], ...]}   # up to 10, by |weight| desc
      type is one of: stock, etf, option, treasury, cash, swap, other

  def rate(fund, http) -> dict | None          (only if RATES is True)
      returns {"value_pct": number, "as_of": "YYYY-MM-DD", "source_url": str}, or None when this fund's rate
      isn't published in readable form (it is then reported for manual review).

Weights and rates come exactly as the issuer publishes them (no calculation). Raise an exception on any doubt;
the refresh keeps the fund's last good values and reports the failure.
"""
