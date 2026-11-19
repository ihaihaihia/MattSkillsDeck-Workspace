"""Eastmoney historical daily bars with turnover and derived float market cap.

Eastmoney's K-line volume is in lots (手); TDX's local volume is in shares.
The turnover field is a percentage of float shares. Therefore:

    float_shares = volume_lots * 100 / (turnover_pct / 100)

This gives a historical, look-ahead-free float market cap for a specific date.

On this Windows host Python's OpenSSL/curl stack is rejected by Eastmoney's
edge, while .NET's ``Invoke-RestMethod`` succeeds. The fetch therefore tries
Python first and falls back to a small PowerShell helper.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Iterable
from urllib.parse import urlencode
from urllib.request import Request, urlopen

CACHE_DIR = Path(__file__).resolve().parents[1] / "data" / "cache" / "eastmoney"
_KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Referer": "https://quote.eastmoney.com/",
}
_PS_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$r = Invoke-RestMethod -Uri $env:EM_URL -Headers @{
  'User-Agent' = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36'
  'Referer' = 'https://quote.eastmoney.com/'
} -TimeoutSec 30
$r | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $env:EM_OUT -Encoding UTF8
"""


@dataclass(frozen=True)
class EastmoneyBar:
    date: int
    open: float
    close: float
    high: float
    low: float
    volume_lots: int
    amount: float
    turnover_pct: float


def _secid(market: str, code: str) -> str:
    return ("1." if market == "sh" else "0.") + code


def _fetch_json_powershell(url: str, timeout: float) -> dict:
    powershell = shutil.which("powershell.exe") or shutil.which("powershell")
    if powershell is None:
        raise RuntimeError("PowerShell is not available for the Eastmoney fallback")
    with tempfile.TemporaryDirectory(prefix="longtou-em-") as tmp:
        out = Path(tmp) / "payload.json"
        env = os.environ.copy()
        env["EM_URL"] = url
        env["EM_OUT"] = str(out)
        completed = subprocess.run(
            [powershell, "-NoProfile", "-NonInteractive", "-Command", _PS_SCRIPT],
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout + 15,
        )
        if completed.returncode != 0 or not out.exists():
            raise RuntimeError(
                "Eastmoney PowerShell fallback failed: "
                + (completed.stderr or completed.stdout or "no output")[:500]
            )
        return json.loads(out.read_text(encoding="utf-8-sig"))


def _fetch_json(url: str, timeout: float) -> dict:
    try:
        request = Request(url, headers=_HEADERS)
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception as first:
        try:
            return _fetch_json_powershell(url, timeout)
        except Exception as second:
            raise RuntimeError(f"Eastmoney fetch failed: {first}; fallback: {second}") from first


def _kline_url(market: str, code: str, beg: str, end: str) -> str:
    params = {
        "secid": _secid(market, code),
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "klt": "101",
        "fqt": "0",  # unadjusted, matching TDX daily prices
        "beg": beg,
        "end": end,
        "ut": "7eea3edcaed734bea9cbfc24409ed989",
    }
    return _KLINE_URL + "?" + urlencode(params)


def fetch_daily(
    market: str,
    code: str,
    beg: str = "20200101",
    end: str = "20991231",
    timeout: float = 15.0,
    use_cache: bool = True,
) -> list[EastmoneyBar]:
    cache_path = CACHE_DIR / f"{market}{code}_{beg}_{end}.json"
    if use_cache and cache_path.exists():
        payload = json.loads(cache_path.read_text(encoding="utf-8-sig"))
    else:
        payload = _fetch_json(_kline_url(market, code, beg, end), timeout)
        if use_cache:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    rows = (payload.get("data") or {}).get("klines") or []
    bars: list[EastmoneyBar] = []
    for row in rows:
        parts = row.split(",")
        if len(parts) < 11:
            continue
        try:
            bars.append(
                EastmoneyBar(
                    date=int(parts[0].replace("-", "")),
                    open=float(parts[1]),
                    close=float(parts[2]),
                    high=float(parts[3]),
                    low=float(parts[4]),
                    volume_lots=int(float(parts[5])),
                    amount=float(parts[6]),
                    turnover_pct=float(parts[10]),
                )
            )
        except ValueError:
            continue
    return bars


def bar_on(bars: Iterable[EastmoneyBar], date: int) -> EastmoneyBar | None:
    for bar in bars:
        if bar.date == date:
            return bar
    return None


def float_cap_from_bar(bar: EastmoneyBar) -> tuple[float, float] | None:
    """Return (float_shares, float_mcap_yi) or None when turnover is unusable."""
    if bar.turnover_pct <= 0 or bar.close <= 0 or bar.volume_lots <= 0:
        return None
    volume_shares = bar.volume_lots * 100.0
    float_shares = volume_shares / (bar.turnover_pct / 100.0)
    float_mcap_yi = float_shares * bar.close / 1e8
    return float_shares, float_mcap_yi


def fetch_float_cap(market: str, code: str, date: int) -> tuple[float, float, EastmoneyBar] | None:
    bars = fetch_daily(market, code)
    bar = bar_on(bars, date)
    if bar is None:
        return None
    derived = float_cap_from_bar(bar)
    if derived is None:
        return None
    float_shares, float_mcap_yi = derived
    return float_shares, float_mcap_yi, bar
