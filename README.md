# AetherBot

[![tests](https://github.com/malwar77/aetherbot/actions/workflows/tests.yml/badge.svg?branch=main)](https://github.com/malwar77/aetherbot/actions/workflows/tests.yml)

A production-oriented, safety-first open-source crypto trading bot for
Python 3.11+, inspired by Freqtrade. CCXT multi-exchange, dry-run paper
trading as the hard default, a local-LLM advisory layer (Ollama — **no API
keys**), Telegram + Web UI control, SQLite persistence, backtesting with
honest benchmark reporting, and an optional sklearn ML module.

> ## ⚠️ SAFETY WARNINGS — READ FIRST
>
> - **This software is for EDUCATIONAL purposes.**
> - **Live trading can lead to TOTAL LOSS OF CAPITAL.**
> - **Always start in dry-run mode.** It is the default and the mandatory
>   first mode.
> - **Never risk money you cannot afford to lose.**
> - **Nothing in this project is financial advice.** No strategy, backtest,
>   or AI annotation here is a promise or expectation of gains.
> - Managing trades on behalf of other people may require licenses in your
>   jurisdiction. Verify independently — this software is for the account
>   owner themselves.

---

## Core design principles

1. **Risk in code, never in prompts.** Every order — paper or live — passes
   the `RiskManager` veto (`aetherbot/risk/risk_manager.py`): max open
   trades, stake limits, daily loss halt, max drawdown halt, loss-streak
   cooldown, stop sanity. No AI output can bypass it.
2. **Dry-run is the hard default.** The paper simulator fills every order
   against real market prices with simulated fees. Live mode requires
   **three human-only fields** set by manually editing the config file:
   `mode.dry_run: false` + `live_confirmation.confirmed_live: true` +
   `live_confirmation.risk_disclosure_accepted: true` with a timestamp.
   No CLI flag, code path, agent, or LLM can set them — the bot refuses to
   start live otherwise (`TradingModeError`).
3. **The AI is advisory with negative-only power.** The Ollama LLM brain
   annotates every proposal; a verdict of `against` can *skip* a signal
   (an advisory veto). It can never create, resize, or approve a trade,
   and its output is whitelisted to summary/agreement/notes — hallucinated
   fields (direction, stake, mode, api keys) are dropped by construction.
   `questions`/`supports` are commentary only. This mirrors the
   RegimeDesk contract and is enforced by tests.
4. **No lookahead bias.** The engine only ever acts on the last CLOSED
   candle; the ML module trains with shuffle-free splits and causal
   features.

## Project structure

```
aetherbot/
├── aetherbot/
│   ├── config.py                # pydantic config + LIVE-MODE GATE
│   ├── main.py                  # CLI: start | backtest | download-data | create-strategy | web
│   ├── ta.py                    # EMA / RSI / ATR (tested vs reference values)
│   ├── engine/
│   │   ├── bot.py               # core trading loop (entries, stops, ROI, trailing)
│   │   ├── force.py             # force-enter/exit — still gated
│   │   └── strategy/            # Strategy base class + resolver/template
│   ├── exchange/
│   │   ├── exchange.py          # CCXT wrapper (spot + USDT-M futures, rate limits)
│   │   └── dry_run.py           # paper simulator (default venue)
│   ├── risk/
│   │   ├── risk_manager.py      # THE veto authority
│   │   └── position_sizing.py   # pure fixed-risk calculator
│   ├── ai/
│   │   ├── brain.py             # Ollama advisory LLM (no API keys)
│   │   └── ml.py                # optional FreqAI-style sklearn module
│   ├── backtest/backtester.py   # + buy&hold / EMA-cross benchmark verdicts
│   ├── data/data.py             # OHLCV download + live/file candle feed
│   ├── persistence/models.py    # SQLAlchemy (SQLite by default)
│   ├── telegram/handler.py      # /status /profit /forcebuy /forcesell /pause ...
│   └── webui/                   # live FastAPI dashboard (data-only)
├── strategies/                  # runnable examples: RsiEmaCross, EmaCrossStrategy,
│                                 # DonchianBreakout (education, not recommendations)
├── config/config.example.yaml   # dry-run example (the default mode)
├── tests/                       # 51 tests: math, gates, AI contract, engine
├── Dockerfile · docker-compose.yml (includes an Ollama sidecar)
└── requirements.txt
```

## Quickstart

```bash
git clone https://github.com/malwar77/aetherbot.git && cd aetherbot
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 1. optional: the AI layer works fully local, no API keys
ollama pull llama3.2

# 2. run in DRY-RUN (default — paper fills, no real orders)
python -m aetherbot.main start --config config/config.example.yaml

# 3. open the live dashboard
open http://127.0.0.1:8080

# other commands
python -m aetherbot.main download-data --days 90        # OHLCV history
python -m aetherbot.main backtest --pair BTC/USDT       # + benchmark verdict
python -m aetherbot.main create-strategy MyStrategy     # scaffold
python -m aetherbot.main web --config config/config.example.yaml
```

## Web dashboard on your LAN

`aetherbot web` serves a terminal-style dashboard — black background,
green for profit, red for loss, blue for information, live-pulsing
status dot, auto-refresh every 5 seconds:

```
python -m aetherbot.main web --config config/config.example.yaml
# prints both URLs:
#   this machine:  http://127.0.0.1:8080
#   on your LAN:   http://192.168.x.x:8080   <- open from your phone
```

The dashboard is READ-ONLY by design: no trade controls exist on the
web surface. Anyone on your Wi-Fi can view it; nobody can trade from
it. To restrict it to this machine only, set `webui host: "127.0.0.1"`
in the YAML.

Historical and dry-run results do not imply future performance. Live
trading can lead to total loss of capital.```

## New here? Start here

AetherBot is a self-hosted crypto trading bot in the spirit of
Freqtrade: dry-run paper trading as the hard default, risk rules in
code, backtests with honest benchmarks, and a fully local AI layer
(Ollama) that only advises. No cloud, no signup, no mandatory API
keys.

**What you need:** Python 3.11+, git, and (optional) Ollama for the
local AI annotations. Exchange API keys are only required if/when YOU
choose to go live — dry-run simulates fills against real market
prices with no keys at all.

1. Clone and set up:
   ```bash
   git clone https://github.com/malwar77/aetherbot.git
   cd aetherbot
   python3 -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   python3 -m pytest tests/ -q        # all green = healthy clone
   ```
2. Optional AI layer (fully local, no keys):
   ```bash
   ollama pull llama3.2
   ```
3. Copy the example config and run DRY-RUN (the default — paper
   fills, no real orders):
   ```bash
   cp config/config.example.yaml config/config.yaml
   python -m aetherbot.main start --config config/config.yaml --once
   python -m aetherbot.main start --config config/config.yaml
   ```
4. Open the live dashboard at http://127.0.0.1:8080

The dashboard is a live *viewing* terminal, not a trading terminal:

- real candlestick charts (TradingView Lightweight Charts, Apache-2.0,
  vendored into the repo) with your trades marked on them
- live candles come from your configured exchange via ccxt; if the
  fetch fails you get an honest error, never a synthetic chart
  (note: Binance geo-blocks some regions/VPNs — switch
  `exchange.name` to `kraken`, `kucoin`, etc. if you see HTTP 451)
- live ticker strip for your whitelisted pairs
- there are NO order buttons: execution belongs to the deterministic
  strategy engine and the RiskManager veto chain, by design
5. Backtest with the mandatory benchmark verdict:
   ```bash
   python -m aetherbot.main download-data --days 90
   python -m aetherbot.main backtest --pair BTC/USDT
   ```

**Stay in dry-run until you understand what the RiskManager blocks
and why.** Going live is deliberately hard and requires you to hand-
edit three confirmation fields in your config — see **Going live
(real money)** below. No command does it for you.

## Windows installation

AetherBot runs natively on Windows — no WSL needed. Open PowerShell:

1. Install Python 3.11+ and git:
   ```
   winget install -e Python.Python.3.12
   winget install -e Git.Git
   ```
   (or download from https://www.python.org/downloads and tick
   "Add python.exe to PATH"). Start a fresh PowerShell afterwards.
2. Clone and set up:
   ```
   git clone https://github.com/malwar77/aetherbot.git
   cd aetherbot
   py -m venv .venv
   .venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   python -m pytest tests/ -q
   ```
   If Activate.ps1 is blocked, run once:
   `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`
   (or use `.venv\Scripts\activate.bat` in cmd).
3. Everywhere the docs say `python3`, use `python` on Windows, e.g.:
   ```
   copy config\config.example.yaml config\config.yaml
   python -m aetherbot.main start --config config\config.yaml --once
   python -m aetherbot.main backtest --pair BTC/USDT
   ```
   The SQLite database and OHLCV parquet files are created under
   `data\` relative to the project — no registry, no system-wide
   installs.
4. Optional local AI layer: install Ollama for Windows from
   https://ollama.com/download/windows, then `ollama pull llama3.2`.
   AetherBot finds it at http://localhost:11434 by default.
5. Morning report beacon (optional): set `AGENT_API_BASE` and
   `AGENT_API_KEY` in your environment, then schedule with Task
   Scheduler instead of cron:
   ```
   schtasks /Create /SC DAILY /ST 07:15 /TN "AetherBotReport" /TR "cmd /c cd /d C:\path\to\aetherbot && .venv\Scripts\python.exe -m aetherbot.main report --config config\config.yaml"
   ```

## Writing a strategy

Subclass `Strategy`, implement the three populate methods:

```python
class MyStrategy(Strategy):
    timeframe = "15m"
    minimal_roi = {"0": 0.04, "30": 0.02, "60": 0.01}
    stoploss = -0.10

    def populate_indicators(self, df, metadata):
        df["rsi"] = rsi(df["close"], 14)
        df["ema_fast"] = ema(df["close"], 9)
        df["ema_slow"] = ema(df["close"], 21)
        return df

    def populate_entry_trend(self, df, metadata):
        df.loc[(df["ema_fast"] > df["ema_slow"])
               & (df["rsi"] < 70) & (df["volume"] > 0), "enter_long"] = 1
        return df

    def populate_exit_trend(self, df, metadata):
        df.loc[df["ema_fast"] < df["ema_slow"], "exit_long"] = 1
        return df
```

Optional overrides: `custom_stoploss`, `custom_stake_amount`, `leverage`
(futures only). Drop the file in `strategies/` and run with
`--strategy MyStrategy`.

## Configuration & keys

- Config is YAML (`config/config.example.yaml`), validated by pydantic.
- **API keys come ONLY from environment variables** — `AETHERBOT_EXCHANGE_API_KEY`,
  `AETHERBOT_EXCHANGE_API_SECRET`, `AETHERBOT_TELEGRAM_TOKEN`. Never in YAML.
- Pair whitelist/blacklist, timeframes 1m–1d, stake modes
  (fixed / percentage / risk_pct fixed-risk sizing).
- Separate configs for dry-run vs live: keep the dry-run example as your
  default and create a second file only if — and after — you accept the
  live-mode risks.

### Going live (only if you accept the risks)

Manually edit your config file — nothing else can do it:

```yaml
mode:
  dry_run: false
  live_confirmation:
    confirmed_live: true
    risk_disclosure_accepted: true
    risk_disclosure_accepted_at: "2026-09-12T12:00:00Z"
```

Then provide exchange keys via env vars and restart. The bot still runs
the full risk stack, and per-exchange stop-loss/take-profit attachment is
attempted where supported while the bot ALWAYS monitors stops locally.

## Telegram control

Set `telegram.enabled: true`, `AETHERBOT_TELEGRAM_TOKEN`, and your
`allowed_user_ids`. Commands: `/status`, `/profit` (real numbers,
including losses), `/pause`, `/resume`, `/forcebuy <pair>`,
`/forcesell <id>`, `/reload`. Force commands pass the SAME risk gates as
signalled entries.

## The AI layer (Ollama, local, no keys)

Every trade proposal is annotated by the LLM brain with a structured read
(`supports` / `questions` / `against`). With `advisory_veto: true`, an
`against` verdict skips the signal — the AI's only power, and it is
negative-only. If Ollama is unreachable, a deterministic local reasoner
annotates from the proposal's own facts (conservative: ambiguous cases
read as `questions`). The optional sklearn ML module (`ai.ml.enabled:
true`) adds a `ml_predict` probability column strategies may use as an
input feature; it trains on shuffled-free splits with a retraining
schedule.

## Going live (real money) — read this first

Live mode is supported but deliberately hard, because losing real
money is easy and the gates exist to make it harder:

1. `aetherbot go-live --config config/config.example.yaml` prints the
   full risk disclosure and audits every live gate (dry_run,
   confirmed_live, risk disclosure acceptance + timestamp, daily
   loss limit, trading pairs).
2. If — and only if — you accept the risks, you enable live mode by
   editing the config YAML BY HAND: `mode.dry_run: false` plus the
   `live_confirmation` block (`confirmed_live: true`,
   `risk_disclosure_accepted: true`,
   `risk_disclosure_accepted_at: <date>`). These are human-only
   fields: no command, agent or automation can set them.
3. Re-run `go-live` until all gates pass. The engine then logs a
   `REAL-MONEY` warning on every single order, and the RiskManager
   (max trades, daily loss halt, drawdown halt, cooldown, ROI,
   trailing stops) stays in charge of every trade, live or paper.

Risks you accept by going live: total loss of your deposit; futures
leverage magnifies losses and liquidations happen fast; slippage,
spreads, funding fees and thin books make live fills worse than
dry-run fills and stops can be gapped or wicked through; exchange
outages, rate limits and network drops can leave positions
unmanaged 24/7; the RiskManager limits but does not prevent losses;
the AI brain is advisory-only and can never create, resize or
approve a trade. Backtest and dry-run results do not predict live
performance.

## Freqtrade bridge (interop, not a code merge)

freqtrade is the industry-standard open-source bot — and GPL-3.0
licensed. AetherBot is MIT. To keep this repo MIT-clean, the bridge
copies NO freqtrade source code. Instead it *generates* a strategy
file that re-bases your own AetherBot strategy on freqtrade's
IStrategy (the two interfaces are column-compatible by design:
`enter_long` / `exit_long` / `populate_*`):

```bash
aetherbot freqtrade-export --strategy EmaCrossStrategy
# -> freqtrade_EmaCrossStrategy.py — drop into a freqtrade
#    user_data/strategies/ and run inside your freqtrade tree
```

The export is a starting point: freqtrade extras (informative pairs,
protections, order types) may need hand-tuning, and you should
backtest inside freqtrade before trusting results. Review the file —
it contains only YOUR code plus the re-base, with the license note
in the header.

## Deriv digit-under simulator (honesty demo)

A faithful port of a sold-for-$20 Deriv DBot martingale strategy
("20$ DERIV AUTO BOT", 2023): bets Volatility 100 Index digit-under
contracts with barrier = (last_digit - 5) % 10, 10x martingale,
stops at +$1 target or -$1,000 max loss. The simulation runs the
exact logic on uniform random digits with Deriv's house edge
modeled (payout = 0.95 x fair odds):

```bash
aetherbot deriv-sim --sessions 2000
```

Typical result: ~86% of sessions reach the +$1 target, ~14% bust
at -$1,000 — mean session PnL about -$100. The high win rate is
the trap; the martingale makes the rare loss catastrophic. One
branch (last digit 5 -> barrier 0) is a guaranteed loss. This is
educational software only: it places no trades and is included so
nobody risks real money on the original bot expecting different
math.

## Morning WhatsApp report (status beacon)

The agent (Base44 Superagent) sends a morning WhatsApp report with
mode, balance, daily PnL, open trades, daily-loss-limit distance and
a watchdog alert if the bot stops checking in. This bot feeds it via
the agent's external API (exact curl examples are in the agent
editor's Developer / API Docs panel):

1. Set env vars (or pass flags): `AGENT_API_BASE` (the agent's API
   root, e.g. https://<host>/api/agents/<agent_id>) and
   `AGENT_API_KEY` (from the editor's Developer panel).
2. `aetherbot report --config config.yaml` — prints the snapshot;
   with AGENT_API_BASE/AGENT_API_KEY set it also sends a STATUS
   BEACON message to the agent, which stores it.
3. Schedule it before the agent's 7:30am ET run, e.g. cron at 07:15
   America/New_York:
   `15 7 * * * cd /path/to/aetherbot && python -m aetherbot.main report --config config.yaml`

Strictly read-only and advisory: the report never places, approves
or alters trades, and live balance is not queried (host-authoritative
numbers only). The RiskManager on this host stays authoritative.

## Backtesting honesty## Backtesting honesty

`aetherbot.main backtest` reports absolute P&L AND a verdict against
buy-and-hold plus a 20/50 EMA-crossover benchmark after fees. A strategy
that fails to beat naive alternatives is reported as failing — loudly.

## Tests

```bash
python -m pytest tests/ -q        # 51 tests
```

Covered: indicator math vs hand-computed/independent reference values,
position sizing, every risk gate, the live-mode config gate, the dry-run
simulator, engine entry/exit cycles, force-command gating, the AI
advisory contract (including adversarial LLM outputs), backtester
sanity, and ML round-trips.

## Docker

```bash
cp .env.example .env   # add keys ONLY here
docker compose up -d   # bot + ollama sidecar
docker compose exec ollama ollama pull llama3.2
```

## License

MIT — see LICENSE. Provided as-is, with no warranty, educational
software only.
## Third-party software

- TradingView Lightweight Charts v4.2.3 (Apache-2.0) — vendored
  unmodified alongside the dashboard assets. Charts render locally
  in your browser; no TradingView servers are contacted.
