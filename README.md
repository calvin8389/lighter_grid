# Lighter Grid

Classic long grid trading bot for [Lighter DEX](https://lighter.xyz) on Arbitrum.

## Quick Start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env — fill in your Lighter private key and account index

python main_loop.py
```

## Configuration

All config in `settings.py`:

```python
active = "BTC"          # trading pair
budget = 1000           # initial capital (USD)

grid = {
    "price_lower": None,        # None = auto (current × 0.95)
    "price_upper": None,        # None = auto (current × 1.05)
    "grid_count": 31,           # number of levels
    "amount_per_order": None,   # None = exchange minimum
}
```

Change `active` to switch coins. Grid parameters auto-calculate if left `None`.  
Validation at startup: lower < upper, amount ≥ exchange minimum, max capital ≤ budget.

## Architecture

```
main_loop.py          — main loop: fetch market → process buys → process sells
lib.py                — shared logic: chain queries, order placement, precision
generate_grid.py      — grid table generator from settings.py
settings.py           — all configuration in one place
```

Each cycle (10s):
1. Fetch BBA → determine buy/sell zones around closest grid level
2. Fetch chain state (positions + active orders)
3. Cancel orders not matching any grid level
4. Fill missing buy orders (skip if upper level has sell)
5. Fill missing sell orders matching position (cancel excess if oversold)

## Files

- `data/grid_setting.json` — computed grid price table
- `data/debug.jsonl` — per-cycle debug data  
- `data/equity.jsonl` — hourly equity snapshots
