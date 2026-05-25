"""共享模块 — 网格运行时全部逻辑。

不依赖任何外部 src/ 模块，所有精度处理内联。
"""
import asyncio, json, os, aiohttp
from dataclasses import dataclass
from decimal import Decimal, ROUND_FLOOR, ROUND_CEILING, ROUND_DOWN
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
import lighter

REST = "https://mainnet.zklighter.elliot.ai"


# ════════════════════════════════════════════════════
#  精度处理 (内联，不依赖 src/)
# ════════════════════════════════════════════════════

@dataclass
class MarketDetails:
    market_id: int | str
    price_tick: float
    size_step: float
    min_order_size: float = 0.0
    min_notional: float = 0.0
    taker_fee_rate: float = 0.0
    maker_fee_rate: float = 0.0


def _to_decimal(v: float) -> Decimal:
    return Decimal(str(v))


def _round_price(price: float, tick: float, side: str) -> float:
    if not tick or tick <= 0: return price
    d = _to_decimal(price); t = _to_decimal(tick)
    r = ROUND_CEILING if side == "buy" else ROUND_FLOOR
    return float((d / t).quantize(Decimal("1"), rounding=r) * t)


def _round_size(size: float, step: float) -> float:
    if not step or step <= 0: return size
    d = _to_decimal(size); s = _to_decimal(step)
    return float((d / s).quantize(Decimal("1"), rounding=ROUND_DOWN) * s)


def normalize(price: float, size: float, side: str, md: MarketDetails) -> tuple[float, float]:
    px = _round_price(price, md.price_tick, side)
    sz = _round_size(size, md.size_step)
    if sz <= 0:
        raise ValueError(f"size {size} rounds to zero (step={md.size_step})")
    return px, sz


# ════════════════════════════════════════════════════
#  环境
# ════════════════════════════════════════════════════

def env():
    return (
        os.environ.get("LIGHTER_PRIVATE_KEY", os.environ.get("API_KEY_PRIVATE_KEY", "")),
        int(os.environ.get("LIGHTER_API_KEY_INDEX", os.environ.get("API_KEY_INDEX", "0"))),
        int(os.environ.get("LIGHTER_ACCOUNT_INDEX", "0")),
    )


# ════════════════════════════════════════════════════
#  网格区域
# ════════════════════════════════════════════════════

def zone_prices(zone):
    return {lv["price"] for lv in zone}


def stray(orders, zone):
    zp = zone_prices(zone)
    return [o for o in orders if o["price"] not in zp], [o for o in orders if o["price"] in zp]


async def refresh_zones(gs):
    """取市价 → 找空层 → 返回 buy_zone, sell_zone, mid。"""
    async with aiohttp.ClientSession() as s:
        async with s.get(f"{REST}/api/v1/orderBookOrders",
                         params={"market_id": str(gs["market_id"]), "limit": "1"}) as resp:
            bba = await resp.json()
    mid = (Decimal(str(bba["bids"][0]["price"])) + Decimal(str(bba["asks"][0]["price"]))) / 2

    levels = gs["levels"]
    best_idx, best_dist = 0, None
    for lv in levels:
        d = abs(Decimal(str(lv["price"])) - mid)
        if best_dist is None or d < best_dist:
            best_dist, best_idx = d, lv["index"]
        elif d == best_dist and Decimal(str(lv["price"])) > Decimal(str(levels[best_idx]["price"])):
            best_idx = lv["index"]

    buy_zone = [lv for lv in levels if lv["index"] < best_idx]
    sell_zone = [lv for lv in levels if lv["index"] > best_idx]
    return buy_zone, sell_zone, float(mid)


# ════════════════════════════════════════════════════
#  链上查询
# ════════════════════════════════════════════════════

async def fetch_chain(gs, token):
    """返回 (btc_pos, buys, sells)。"""
    idx = int(os.environ.get("LIGHTER_ACCOUNT_INDEX", "0"))
    async with aiohttp.ClientSession() as s:
        async with s.get(f"{REST}/api/v1/account", params={"by": "index", "value": str(idx)}) as resp:
            acc = await resp.json()
        async with s.get(f"{REST}/api/v1/accountActiveOrders",
                         params={"account_index": idx, "market_id": gs["market_id"]},
                         headers={"authorization": token}) as resp:
            orders = await resp.json()

    btc_pos = 0.0
    for p in acc["accounts"][0].get("positions", []):
        if p.get("symbol") == gs["symbol"]:
            btc_pos = float(p.get("position", 0) or 0) * int(p.get("sign", 0))

    buys, sells = [], []
    for o in orders.get("orders", []):
        d = {"price": float(o.get("price", 0) or 0),
             "cid": str(o.get("client_order_index", o.get("client_order_id", ""))),
             "order_index": str(o.get("order_index", o.get("order_id", "")))}
        (sells if o.get("is_ask", False) else buys).append(d)
    sells.sort(key=lambda d: d["price"])
    return btc_pos, buys, sells


# ════════════════════════════════════════════════════
#  下单 / 撤单
# ════════════════════════════════════════════════════

async def cancel_one(signer, market_id, order_index, ki):
    try:
        _, _, err = await signer.cancel_order(
            market_index=market_id, order_index=int(order_index),
            skip_nonce=0, nonce=-1, api_key_index=ki)
        return err is None
    except Exception:
        return False


async def place_orders(signer, levels, gs, ki, side):
    is_sell = (side == "sell")
    md = MarketDetails(market_id=gs["market_id"], price_tick=gs["price_tick"],
                       size_step=gs["size_step"], min_order_size=gs["min_base_amount"],
                       min_notional=gs["min_quote_amount"], taker_fee_rate=0, maker_fee_rate=0)
    ok = 0
    for lv in levels:
        px, sz = normalize(lv["price"], gs["amount_per_order"], side, md)
        bs = int(Decimal(str(sz)) / Decimal(str(gs["size_step"])))
        ps = int(Decimal(str(px)) / Decimal(str(gs["price_tick"])))
        try:
            _, _, err = await signer.create_order(
                market_index=gs["market_id"], client_order_index=int.from_bytes(os.urandom(4), "big"),
                base_amount=bs, price=ps, is_ask=is_sell,
                order_type=lighter.SignerClient.ORDER_TYPE_LIMIT,
                time_in_force=lighter.SignerClient.ORDER_TIME_IN_FORCE_GOOD_TILL_TIME,
                reduce_only=is_sell, trigger_price=0, api_key_index=ki)
            if err:
                print(f"  FAIL Lv{lv['index']} @ {lv['price']}: {err}")
            else:
                print(f"  OK   Lv{lv['index']} @ {lv['price']}")
                ok += 1
        except Exception as e:
            print(f"  ERR  Lv{lv['index']} @ {lv['price']}: {e}")
        await asyncio.sleep(0.3)
    if ok:
        print(f"  → {ok}/{len(levels)} {side}单")
    return ok


# ════════════════════════════════════════════════════
#  买单 / 卖单 处理
# ════════════════════════════════════════════════════

async def process_buys(signer, buys, sells, buy_zone, gs, ki):
    """越界清理（vs 全网格）→ 补空档（buy_zone, 上层有卖单则跳过）。"""
    st, buys = stray(buys, gs["levels"])
    if st:
        print(f"  买单: {len(st)} 个越界 {[s['price'] for s in st]}, 逐笔撤")
        for s in st:
            await cancel_one(signer, gs["market_id"], s["order_index"], ki)
    existing = {s["price"] for s in buys}
    sell_above = {s["price"] for s in sells}
    gaps = []
    for lv in sorted(buy_zone, key=lambda lv: lv["price"], reverse=True):
        if lv["price"] in existing:
            continue
        above = gs["levels"][min(lv["index"] + 1, gs["grid_count"] - 1)]
        if above["price"] in sell_above:
            continue
        gaps.append(lv)
    if gaps:
        print(f"  买单空档: {len(gaps)} 个")
        await place_orders(signer, gaps, gs, ki, "buy")


async def process_sells(signer, sells, sell_zone, btc_pos, gs, ki):
    """量检查 → 价格检查 → 补缺口。"""
    amt = gs["amount_per_order"]
    qty = len(sells) * amt

    if qty > btc_pos + amt * 0.5:
        extra = len(sells) - round(btc_pos / amt)
        print(f"  卖单超量: {len(sells)}单 > pos {btc_pos}, 撤 {extra} 个高价单")
        for s in sells[-extra:]:  # sells已按价格升序排列，尾部=最高价
            await cancel_one(signer, gs["market_id"], s["order_index"], ki)
            sells.remove(s)
        return

    if abs(qty - btc_pos) < amt * 0.5:
        st, sells = stray(sells, gs["levels"])
        if st:
            print(f"  卖单: {len(st)} 个越界 {[s['price'] for s in st]}, 逐笔撤")
            for s in st:
                await cancel_one(signer, gs["market_id"], s["order_index"], ki)

    need = max(0, round(btc_pos / amt) - len(sells))
    if need == 0:
        return
    print(f"  卖单缺口: {need} 张")
    existing = {s["price"] for s in sells}
    gaps = [lv for lv in sell_zone if lv["price"] not in existing][:need]
    if gaps:
        await place_orders(signer, gaps, gs, ki, "sell")
