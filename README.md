# GasWatch

Live Ethereum **base fee** and **priority fee** tracker with a graphical desktop app for macOS and Windows.

GasWatch reads the newest block and the last N blocks of fee history from any Ethereum JSON-RPC
node, classifies the network into a gas level with visual status indicators (green when cheap,
red when expensive), and renders the result as a clean report you can copy as Markdown.

```
🟢 LOW ETH GAS REPORT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Base fee   0.2118 Gwei   █░░░░░░░  🟢 LOW
Next block 0.2096 Gwei   ▼ 1.03% 🟢 LOW
Tip fee    0.2037 Gwei   █░░░░░░░  🟢 LOW
Tip range  0.000404 Gwei · 0.2037 Gwei · 1.000 Gwei   20 blk
Max fee    0.4155 Gwei

💰 Estimated cost per transaction
ETH transfer          21000 gas   0.000009 ETH
Token swap           150000 gas   0.000062 ETH
NFT mint             120000 gas   0.000050 ETH
Contract deploy      500000 gas   0.000208 ETH

📦 Block 26,129,598   ·   45.9% gas used
window over 20 blocks:  0.1800 Gwei → 0.2118 Gwei
🔗 via eth.drpc.org   ·   2026-10-05 23:58:04 UTC   just now

Cheap right now. Good moment to send. 🟢 LOW
```

## Install and run from source

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

python run_gui.py            # desktop app
python -m gaswatch once      # one report in the terminal
```

With the package installed (`pip install -e .`) the CLI is available as `gaswatch`.

## Desktop app

Download the prebuilt app, move `GasWatch.app` to your Applications folder and launch it.
No Python needed on the target machine.

macOS builds are produced by `scripts/build_macos.sh`, Windows builds by `scripts/build_windows.ps1`.

First launch is blocked on macOS because the app is not notarised. Either right click the app and
choose **Open**, or run:

```bash
xattr -dr com.apple.quarantine /Applications/GasWatch.app
```

The app window shows:

- a **status pill** in the top right that turns green, amber, orange or red with the gas level
- four metric cards for base fee, tip fee, max fee and block usage, each with its own colour and meter
- an advice line telling you whether it is a good moment to send
- a **Details** tab with the fee window, tip percentiles, transaction cost estimates and RPC health
- a **Markdown** tab with the report rendered as copyable Markdown

Everything is editable from **Settings**: RPC endpoints, timeout, retries, cooldown, history depth,
refresh interval, units, the gas level thresholds and the ETH/USD toggle.

## Gas levels

| Indicator | Level | Base fee | Priority fee |
|-----------|-------|----------|--------------|
| 🟢 | LOW | below 2 gwei | below 0.3 gwei |
| 🟡 | MODERATE | up to 8 gwei | up to 1.5 gwei |
| 🟠 | HIGH | up to 25 gwei | up to 5 gwei |
| 🔴 | EXTREME | above 25 gwei | above 5 gwei |

The overall status is the worse of the base fee and tip fee. Change the cutoffs in
**Settings** or with the `GASWATCH_BASE_LOW_MAX` style environment variables.

## CLI

```
gaswatch                      launch the desktop app
gaswatch once                 print one report
gaswatch watch                refresh on a timer
gaswatch ping                 check every RPC endpoint and report latency
gaswatch endpoints            show configured endpoints
gaswatch thresholds           show the gas level cutoffs
gaswatch config               print the resolved configuration as JSON
gaswatch selftest             verify the RPC reader and the GUI, then exit
```

Global flags work before or after the subcommand:

```
gaswatch --units kwei once
gaswatch once --units kwei
gaswatch once --markdown          raw Markdown instead of colours
gaswatch once --price              include ETH/USD cost estimates
gaswatch once -e https://my.node/rpc
```

## Configuration

Settings are read from, in order of increasing priority:

1. built-in defaults
2. `~/.gaswatch/config.json` (written by the Settings dialog)
3. `.env` in the working directory or `~/.gaswatch/.env`
4. environment variables

| Variable | Meaning | Default |
|----------|---------|---------|
| `GASWATCH_RPC_ENDPOINTS` | comma separated RPC URLs | four public nodes |
| `GASWATCH_RPC_TIMEOUT` | per request timeout, seconds | 12 |
| `GASWATCH_RPC_RETRIES` | retries per request | 2 |
| `GASWATCH_RPC_COOLDOWN` | seconds a failed node is skipped | 45 |
| `GASWATCH_HISTORY_BLOCKS` | blocks of fee history to read | 20 |
| `GASWATCH_BASE_LOW_MAX` | base fee LOW cut, gwei | 2 |
| `GASWATCH_BASE_MODERATE_MAX` | base fee MODERATE cut, gwei | 8 |
| `GASWATCH_BASE_HIGH_MAX` | base fee HIGH cut, gwei | 25 |
| `GASWATCH_TIP_LOW_MAX` | tip LOW cut, gwei | 0.3 |
| `GASWATCH_TIP_MODERATE_MAX` | tip MODERATE cut, gwei | 1.5 |
| `GASWATCH_TIP_HIGH_MAX` | tip HIGH cut, gwei | 5 |
| `GASWATCH_UNITS` | `wei`, `kwei`, `mwei`, `gwei` | `gwei` |
| `GASWATCH_SHOW_USD` | add ETH/USD estimates | 0 |
| `GUI_REFRESH_INTERVAL` | GUI auto refresh, seconds | 10 |
| `GASWATCH_CONFIG_DIR` | config directory | `~/.gaswatch` |

Public endpoints are rate limited and occasionally down. For reliable use point
`GASWATCH_RPC_ENDPOINTS` at your own node, Infura, Alchemy or QuickNode.

## How the numbers are calculated

- **Base fee** comes from `baseFeePerGas` of the latest block. It is burned by the protocol and
  rises when a block is more than half full.
- **Next block** base fee is the last entry of `eth_feeHistory`, which already accounts for the
  fullness of the block that just closed. The arrow shows which way it is heading.
- **Tip fee** is the median of the p10/p50/p90 rewards in `eth_feeHistory`, raised to at least the
  value reported by `eth_maxPriorityFeePerGas` when that node answers usefully. Public nodes often
  return `0x0` for that call, which is why the fee history is the primary source.
- **Max fee** is base fee plus tip fee, a reasonable `maxFeePerGas` for a normal transaction.
- **Cost estimates** multiply that by standard gas limits: 21k transfer, 120k NFT mint, 150k swap,
  500k contract deploy.

## RPC failure handling

GasWatch never shows a raw stack trace. When every endpoint fails it reports the most useful
underlying reason and what to try next:

- connection refused or reset, DNS failure, TLS problems and proxy blocks are each named
- timeouts are reported as timeouts, malformed JSON-RPC responses as malformed responses
- endpoints are ranked by success count and latency, and a failing node is put in a cooldown so
  it is skipped on the next request instead of costing another timeout
- a request is retried with exponential backoff and then moved to the next endpoint
- in the GUI a failure turns the status pill red, marks the cards as errored, lists each endpoint
  with its error, and keeps retrying on the refresh interval
- programming errors such as `KeyError` are re-raised instead of being misreported as network faults

## Layout

```
src/gaswatch/
  config.py        defaults, JSON and environment configuration, validation
  exceptions.py    exception types and human readable failure messages
  rpc.py           RPCPool, endpoint health, cooldowns, failover, retries
  fees.py          FeeSnapshot, block and fee history reads, cost estimates
  price.py         optional ETH/USD lookup with failover and caching
  thresholds.py    gas levels, colours and meters
  formatting.py    Markdown, ANSI terminal and plain text renderers
  gui.py           tkinter desktop app and settings dialog
  cli.py           argument parsing and subcommands
run_gui.py         desktop entry point, also the PyInstaller entry
tests/             unittest suite
scripts/           icon generation and per platform builds
```

## Tests

```bash
.venv/bin/python tests/test_gaswatch.py                     # offline suite
GASWATCH_LIVE_TESTS=1 .venv/bin/python tests/test_gaswatch.py   # plus a live RPC read
```

`gaswatch selftest` is the packaging check: it reads a live block, renders the report, builds the
real Tk window, applies a snapshot to the widgets and prints what it found. Run it against the
built app to confirm the bundle carries everything it needs.

```bash
GasWatch.app/Contents/MacOS/GasWatch selftest --out /tmp/report.txt
```