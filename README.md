# Lighter Grid

Classic long grid trading bot for [Lighter DEX](https://lighter.xyz) on Arbitrum.

## Quick Start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Copy and edit env
cp .env.example .env
# Fill in your Lighter private key and account index

# Adjust grid config (optional)
vim config/btc_long.yaml

# Run
python main_loop.py
```

## Architecture

```
main_loop.py        — main loop: fetch market → process buys → process sells
lib.py              — shared logic: chain queries, order placement, precision
generate_grid.py    — one-time grid table generator from config
config/btc_long.yaml — grid parameters (range, levels, amount)
```

Each cycle (10s):
1. Fetch current BBA → determine buy/sell zones
2. Fetch chain state (positions + active orders)
3. Cancel stray orders not matching grid levels
4. Fill missing buy orders in buy zone (skip if upper level has sell)
5. Fill missing sell orders matching position (cancel excess if oversold)

## Config

```yaml
symbol: BTC
market_id: 1
price_lower: 76000.0
price_upper: 78000.0
grid_count: 20          # number of intervals (levels = grid_count + 1)
amount_per_order: 0.0002
```

Grid is generated once at startup from config values. Same config always produces the same grid.
