"""生成理论网格价格表 grid_setting.json。

依赖: config/presets/xxx_long_grid.yaml
输出: data/grid_setting.json
"""
import asyncio, json, os, sys, aiohttp
from decimal import Decimal, ROUND_FLOOR, ROUND_CEILING, ROUND_DOWN
import yaml

REST = "https://mainnet.zklighter.elliot.ai"
BUDGET = 1000.0  # USDC

async def main():
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config/btc_long.yaml"
    with open(config_path) as f:
        yc = yaml.safe_load(f)

    symbol = yc["symbol"]
    market_id = yc["market_id"]

    # 拉取市场参数
    async with aiohttp.ClientSession() as s:
        async with s.get(f"{REST}/api/v1/orderBooks") as resp:
            books = await resp.json()
    meta = next(b for b in books["order_books"] if b["symbol"] == symbol)
    tick = 10 ** -meta["supported_price_decimals"]
    step = 10 ** -meta["supported_size_decimals"]
    min_base = float(meta["min_base_amount"])
    min_quote = float(meta["min_quote_amount"])
    maker_fee = float(meta["maker_fee"])
    taker_fee = float(meta["taker_fee"])

    print(f"{symbol} tick={tick} step={step} min_base={min_base} fee={maker_fee}/{taker_fee}")

    # 网格参数从 config 取，对齐 tick
    t = Decimal(str(tick)); s = Decimal(str(step))
    lower = float((Decimal(str(yc["price_lower"])) / t).to_integral_value(rounding=ROUND_FLOOR) * t)
    upper = float((Decimal(str(yc["price_upper"])) / t).to_integral_value(rounding=ROUND_CEILING) * t)
    n = yc["grid_count"]
    if n < 3: n = 3

    # 每单量：对齐 step，不小于 min_base，满足最低 notional
    amt = float((Decimal(str(yc.get("amount_per_order", min_base))) / s).to_integral_value(rounding=ROUND_DOWN) * s)
    if amt < min_base:
        amt = float((Decimal(str(min_base)) / s).to_integral_value(rounding=ROUND_CEILING) * s)
    if amt * lower < min_quote:
        amt = float((Decimal(str(min_quote / lower)) / s).to_integral_value(rounding=ROUND_CEILING) * s)
    amt = float((Decimal(str(amt)) / s).to_integral_value(rounding=ROUND_DOWN) * s)

    # 校验满仓不超过 budget
    max_cap = n * amt * upper
    if max_cap > BUDGET * 1.1:
        print(f"WARN: max_cap=${max_cap:,.0f} > budget=${BUDGET:,.0f}")

    # tick 步长取整
    tick_lo = int(Decimal(str(lower)) / t)
    tick_hi = int(Decimal(str(upper)) / t)
    tick_span = tick_hi - tick_lo
    tick_step = round(tick_span / (n - 1))
    tick_hi_adj = tick_lo + tick_step * (n - 1)
    upper_adj = float(Decimal(str(tick_hi_adj)) * t)
    interval = float(Decimal(str(tick_step)) * t)

    # 生成全部理论价格
    levels = [{"index": i, "price": float(Decimal(str(tick_lo + i * tick_step)) * t)} for i in range(n)]

    setting = {
        "symbol": symbol, "market_id": market_id,
        "price_lower": lower, "price_upper": upper_adj,
        "grid_count": n, "interval": interval,
        "amount_per_order": amt,
        "price_tick": tick, "size_step": step,
        "min_base_amount": min_base, "min_quote_amount": min_quote,
        "maker_fee_rate": maker_fee, "taker_fee_rate": taker_fee,
        "levels": levels,
    }

    os.makedirs("data", exist_ok=True)
    with open("data/grid_setting.json", "w") as f:
        json.dump(setting, f, indent=2)

    print(f"grid: {n}L [{lower:.1f}, {upper_adj:.1f}] interval=${interval:.4f} amt={amt}")
    print(f"max_cap: ${n * amt * upper_adj:,.0f}  (budget=${BUDGET:,.0f})")
    print(f"written: data/grid_setting.json ({n} levels)")
    for lv in levels:
        print(f"  Lv{lv['index']:>2}: {lv['price']:>8.1f}")

asyncio.run(main())
