"""FreqAI-style optional ML module (sklearn; no cloud, no keys).

Pipeline:
- build_features: RSI, EMA slopes, ATR%, returns — deterministic helpers.
- make_labels: forward-return sign over `horizon` candles (binary).
- train: GradientBoosting | RandomForest | Logistic on features/labels.
- predict: probability the next candle's forward return is positive ->
  merged into the strategy dataframe as column `ml_predict`.

Retraining schedule: ModelStore.last_trained + retrain_every_days.

ML output is an INPUT COLUMN only — strategies choose to use it; it never
bypasses the risk manager, and the LLM brain never sees it as an order.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Literal

import pandas as pd

from ..ta import atr, ema, rsi

log = logging.getLogger("aetherbot.ai.ml")

ModelKind = Literal["gradient_boosting", "random_forest", "logistic"]


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    """Deterministic feature frame from raw OHLCV."""
    out = pd.DataFrame(index=df.index)
    out["ret1"] = df["close"].pct_change()
    out["ret3"] = df["close"].pct_change(3)
    out["volatility"] = df["close"].pct_change().rolling(14).std()
    out["rsi"] = rsi(df["close"], 14)
    out["ema9_slope"] = (ema(df["close"], 9) - ema(df["close"], 9).shift(3)) \
        / df["close"]
    out["atr_pct"] = atr(df, 14) / df["close"]
    out["volume_z"] = (df["volume"] - df["volume"].rolling(20).mean()) / \
        (df["volume"].rolling(20).std() + 1e-12)
    return out


def make_labels(df: pd.DataFrame, horizon: int = 4) -> pd.Series:
    """Binary label: forward return over `horizon` candles is positive."""
    fwd = df["close"].shift(-horizon) / df["close"] - 1
    return (fwd > 0).astype(int)


def _make_model(kind: ModelKind):
    if kind == "gradient_boosting":
        from sklearn.ensemble import GradientBoostingClassifier
        return GradientBoostingClassifier(n_estimators=100, max_depth=3,
                                           random_state=42)
    if kind == "random_forest":
        from sklearn.ensemble import RandomForestClassifier
        return RandomForestClassifier(n_estimators=100, max_depth=5,
                                       random_state=42)
    from sklearn.linear_model import LogisticRegression
    return LogisticRegression(max_iter=1000)


class ModelStore:
    """Trains, persists (joblib-free: keep it stdlib+sklearn via
   pickle), and re-trains on a schedule."""

    def __init__(self, model_dir: str = "models", kind: ModelKind =
                 "gradient_boosting", retrain_every_days: int = 7):
        self.dir = Path(model_dir)
        self.dir.mkdir(exist_ok=True)
        self.kind = kind
        self.retrain_every_days = retrain_every_days
        self.model = None
        self.trained_at: pd.Timestamp | None = None

    def _model_path(self) -> Path:
        return self.dir / ("%s_model.pkl" % self.kind)

    def load(self) -> bool:
        import pickle
        p = self._model_path()
        if not p.exists():
            return False
        with open(p, "rb") as f:
            self.model, self.trained_at = pickle.load(f)
        return True

    def needs_retrain(self, now: pd.Timestamp) -> bool:
        if self.model is None:
            return True
        if self.trained_at is None:
            return True
        return (now - self.trained_at).days >= self.retrain_every_days

    def train(self, df: pd.DataFrame, horizon: int = 4) -> dict:
        """Train on a strategies' indicator dataframe. Returns metrics."""
        from sklearn.metrics import accuracy_score
        from sklearn.model_selection import train_test_split
        import pickle
        feats = build_features(df).dropna()
        labels = make_labels(df, horizon).loc[feats.index].dropna()
        feats = feats.loc[labels.index]
        if len(feats) < 100:
            raise ValueError("need >= 100 labeled candles to train, got %d"
                             % len(feats))
        X_train, X_test, y_train, y_test = train_test_split(
            feats, labels, test_size=0.25, shuffle=False)  # no lookahead
        model = _make_model(self.kind)
        model.fit(X_train, y_train)
        acc = accuracy_score(y_test, model.predict(X_test))
        self.model = model
        self.trained_at = pd.Timestamp.utcnow().tz_localize(None)
        with open(self._model_path(), "wb") as f:
            pickle.dump((self.model, self.trained_at), f)
        metrics = {"accuracy": round(float(acc), 4),
                   "n_train": len(X_train), "n_test": len(X_test),
                   "kind": self.kind, "horizon": horizon,
                   "trained_at": str(self.trained_at)}
        (self.dir / "last_train_metrics.json").write_text(
            json.dumps(metrics, indent=2))
        log.info("ML model trained: %s", metrics)
        return metrics

    def predict_proba(self, df: pd.DataFrame) -> pd.Series:
        """P(forward return positive) aligned to df.index."""
        if self.model is None:
            raise RuntimeError("model not trained — call train() first")
        feats = build_features(df)
        valid = feats.dropna()
        proba = pd.Series(float("nan"), index=df.index)
        if len(valid):
            p = self.model.predict_proba(valid)[:, 1]
            proba.loc[valid.index] = p
        return proba
