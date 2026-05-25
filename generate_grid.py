"""生成理论网格价格表 grid_setting.json。

读取 settings.py，拉取链上参数，计算网格，写 grid_setting.json。
"""
import asyncio, json, os, sys, aiohttp
from decimal import Decimal, ROUND_FLOOR, ROUND_CEILING, ROUND_DOWN
import settings

REST = "https://mainnet.zklighter.elliot.ai"


async def main():
    symbol = settings.active.upper()
    budget = float(settings.budget)
    g = settings.grid

    # 拉取市场参数
    async with aiohttp.ClientSession() as s:
        async with s.get(f"{REST}/api/v1/orderBooks") as resp:
            books = await resp.json()
    meta = next(b for b in books["order_books"] if b["symbol"].upper() == symbol)
    market_id = meta["market_id"]
    tick = 10 ** -meta["supported_price_decimals"]
    step = 10 ** -meta["supported_size_decimals"]
    min_base = float(meta["min_base_amount"])
    min_quote = float(meta["min_quote_amount"])

    # 回填 settings.chain
    settings.chain["symbol"] = symbol
    settings.chain["market_id"] = market_id
    settings.chain["price_tick"] = tick
    settings.chain["size_step"] = step
    settings.chain["min_base_amount"] = min_base
    settings.chain["min_quote_amount"] = min_quote
    settings.chain["maker_fee_rate"] = float(meta.get("maker_fee", 0) or 0)
    settings.chain["taker_fee_rate"] = float(meta.get("taker_fee", 0) or 0)

    print(f"{symbol} market_id={market_id} tick={tick} step={step} min_base={min_base}")

    # 取当前价（自动计算 price_lower/upper 时用到）
    async with aiohttp.ClientSession() as s:
        async with s.get(f"{REST}/api/v1/orderBookOrders",
                         params={"market_id": str(market_id), "limit": "1"}) as resp:
            bba = await resp.json()
    price = (float(bba["bids"][0]["price"]) + float(bba["asks"][0]["price"])) / 2

    # 网格区间：config 有值用 config，否则自动 ±5%
    t = Decimal(str(tick)); s = Decimal(str(step))
    lower = g.get("price_lower") or None
    upper = g.get("price_upper") or None
    if lower is None:
        lower = float((Decimal(str(price * 0.95)) / t).to_integral_value(rounding=ROUND_FLOOR) * t)
    else:
        lower = float((Decimal(str(lower)) / t).to_integral_value(rounding=ROUND_FLOOR) * t)
    if upper is None:
        upper = float((Decimal(str(price * 1.05)) / t).to_integral_value(rounding=ROUND_CEILING) * t)
    else:
        upper = float((Decimal(str(upper)) / t).to_integral_value(rounding=ROUND_CEILING) * t)

    # 每单量
    amt = g.get("amount_per_order") or None
    if amt is None:
        amt = float((Decimal(str(min_base)) / s).to_integral_value(rounding=ROUND_CEILING) * s)
    else:
        amt = float((Decimal(str(amt)) / s).to_integral_value(rounding=ROUND_DOWN) * s)
    if amt < min_base:
        amt = float((Decimal(str(min_base)) / s).to_integral_value(rounding=ROUND_CEILING) * s)
    if amt * lower < min_quote:
        amt = float((Decimal(str(min_quote / lower)) / s).to_integral_value(rounding=ROUND_CEILING) * s)
    amt = float((Decimal(str(amt)) / s).to_integral_value(rounding=ROUND_DOWN) * s)

    # 层数
    n = g.get("grid_count", 20)
    if n < 3: n = 3

    # 校验满仓
    max_cap = n * amt * upper
    if max_cap > budget * 1.1:
        print(f"WARN: max_cap=${max_cap:,.0f} > budget=${budget:,.0f}")

    # tick 步长取整
    tick_lo = int(Decimal(str(lower)) / t)
    tick_hi = int(Decimal(str(upper)) / t)
    tick_span = tick_hi - tick_lo
    tick_step = round(tick_span / (n - 1))
    tick_hi_adj = tick_lo + tick_step * (n - 1)
    upper_adj = float(Decimal(str(tick_hi_adj)) * t)
    interval = float(Decimal(str(tick_step)) * t)

    levels = [{"index": i, "price": float(Decimal(str(tick_lo + i * tick_step)) * t)} for i in range(n)]

    setting = {
        "symbol": symbol, "market_id": market_id,
        "price_lower": lower, "price_upper": upper_adj,
        "grid_count": n, "interval": interval,
        "amount_per_order": amt,
        "price_tick": tick, "size_step": step,
        "min_base_amount": min_base, "min_quote_amount": min_quote,
        "maker_fee_rate": settings.chain["maker_fee_rate"],
        "taker_fee_rate": settings.chain["taker_fee_rate"],
        "levels": levels,
    }

    os.makedirs("data", exist_ok=True)
    with open("data/grid_setting.json", "w") as f:
        json.dump(setting, f, indent=2)

    print(f"grid: {n}L [{lower:.1f}, {upper_adj:.1f}] interval=${interval:.4f} amt={amt}")
    print(f"max_cap: ${n * amt * upper_adj:,.0f}  (budget=${budget:,.0f})")

asyncio.run(main())
