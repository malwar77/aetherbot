"""Data: historical download + live candle feed.

- download: paginated OHLCV fetch via ccxt public API, stored parquet.
- feed: live candles either from the exchange (public polling — no keys
  needed for data) or from a parquet/csv fixture (offline/testing).
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

import pandas as pd

log = logging.getLogger("aetherbot.data")


def timeframe_seconds(tf: str) -> int:
    mult = {"m": 60, "h": 3600, "d": 86400, "w": 604800}
    try:
        return int(tf[:-1]) * mult[tf[-1]]
    except (KeyError, ValueError):
        raise ValueError("invalid timeframe %r" % tf)


def download_ohlcv(exchange, pair: str, timeframe: str, days: int,
                   out_dir: str = "data") -> Path:
    """Download `days` of OHLCV for pair into data/<pair>_<tf>.parquet.
    Paginates with the timeframe step; respects ccxt rate limits."""
    Path(out_dir).mkdir(exist_ok=True)
    since = int((time.time() - days * 86400) * 1000)
    all_rows: list = []
    while True:
        batch = exchange.fetch_ohlcv(pair, timeframe, limit=1000)
        if not batch:
            break
        all_rows.extend(batch)
        log.info("%s %s: %d candles so far", pair, timeframe, len(all_rows))
        if len(batch) < 1000 or batch[-1][0] >= time.time() * 1000:
            break
        since = batch[-1][0] + 1
    if not all_rows:
        raise RuntimeError("no data returned for %s" % pair)
    df = pd.DataFrame(all_rows,
                      columns=["timestamp", "open", "high", "low", "close",
                               "volume"]).drop_duplicates("timestamp")
    out = Path(out_dir) / ("%s_%s.parquet" % (pair.replace("/", "_"),
                                              timeframe))
    df.to_parquet(out)
    log.info("saved %d candles -> %s", len(df), out)
    return out


class CandleFeed:
    """Provides per-pair OHLCV dataframes.

    source=exchange: polls the live exchange (public endpoints).
    source=file: replays a parquet/csv fixture — for offline testing and
    deterministic backtests. In live mode the engine NEVER acts on the
    still-open candle: signals come from the last CLOSED candle.
    """

    def __init__(self, source: str = "exchange", exchange=None,
                 fixture_dir: str = "data", pairs: list[str] | None = None):
        self.source = source
        self.exchange = exchange
        self.fixture_dir = Path(fixture_dir)
        self._files: dict[str, Path] = {}
        if pairs:
            self._index_fixtures(pairs)

    def _index_fixtures(self, pairs: list[str]) -> None:
        files = sorted(self.fixture_dir.glob("*")) if \
            self.fixture_dir.exists() else []
        for p in pairs:
            stem = p.replace("/", "_")
            match = [f for f in files
                     if f.name.startswith(stem + "_")
                     and f.suffix in (".parquet", ".csv")]
            if match:
                self._files[p] = match[0]

    def ohlcv(self, pair: str, timeframe: str, limit: int = 200) \
            -> pd.DataFrame:
        if self.source == "file":
            if pair not in self._files:
                raise FileNotFoundError(
                    "no fixture for %s in %s" % (pair, self.fixture_dir))
            f = self._files[pair]
            df = pd.read_parquet(f) if f.suffix == ".parquet" \
                else pd.read_csv(f)
            return df.tail(limit).reset_index(drop=True)
        raw = self.exchange.fetch_ohlcv(pair, timeframe, limit=limit)
        return pd.DataFrame(raw, columns=["timestamp", "open", "high",
                                           "low", "close", "volume"])

    @staticmethod
    def closed(df: pd.DataFrame) -> pd.DataFrame:
        """Drop the still-forming candle (last row when it is newer than
        `limit` timeframes ago). The engine always acts on closed data."""
        return df.iloc[:-1] if len(df) > 1 else df
