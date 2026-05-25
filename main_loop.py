"""主循环 — 持续运行，每 10s 一轮。

启动: 01 生成理论基准
每轮: 取市价 → 划买卖区 → 查链 → 处理买单 → 处理卖单
"""
import asyncio, aiohttp, json, os, signal, sys, time
from datetime import datetime
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
import lighter
from lib import refresh_zones, fetch_chain, process_buys, process_sells, env

REST = "https://mainnet.zklighter.elliot.ai"


async def main():
    # ── 启动: 01 生成理论基准 ──
    print("启动: 生成理论网格...")
    import subprocess
    subprocess.run([sys.executable, "generate_grid.py"], check=True)
    with open("data/grid_setting.json") as f:
        gs = json.load(f)
    print(f"网格: {gs['symbol']} {gs['grid_count']}层 [{gs['price_lower']:.0f}-{gs['price_upper']:.0f}] "
          f"interval={gs['interval']} amt={gs['amount_per_order']}\n")

    pk, ki, account_index = env()
    signer = lighter.SignerClient(url=REST, account_index=account_index, api_private_keys={ki: pk})

    # 优雅退出
    stop = False
    def _shutdown():
        nonlocal stop
        print("\n收到退出信号, 当前循环结束后停止...")
        stop = True
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try: loop.add_signal_handler(sig, _shutdown)
        except NotImplementedError: pass

    # 调试日志
    import logging
    dbg = logging.getLogger("dbg")
    dbg.setLevel(logging.DEBUG)
    dbg.propagate = False
    os.makedirs("data", exist_ok=True)
    fh = logging.FileHandler("data/debug.jsonl")
    fh.setFormatter(logging.Formatter("%(message)s"))
    dbg.addHandler(fh)

    cycle = 0
    last_snapshot = 0.0
    while not stop:
        cycle += 1
        t0 = time.time()
        try:
            # 鉴权（首次 + 每 9 分钟刷新）
            if cycle == 1 or time.time() - getattr(signer, '_auth_ts', 0) > 540:
                token, err = signer.create_auth_token_with_expiry(api_key_index=ki)
                signer._auth_ts = time.time()
                if err:
                    print(f"auth error: {err}"); await asyncio.sleep(10); continue

            # 1. 刷新买卖区
            buy_zone, sell_zone, mid = await refresh_zones(gs)

            # 2. 查链
            btc_pos, buys, sells = await fetch_chain(gs, token)

            # 调试: 记录原始值
            import json as _json
            amt = gs["amount_per_order"]
            dbg.info(_json.dumps({
                "ts": datetime.now().strftime("%H:%M:%S"), "cycle": cycle,
                "btc_pos": btc_pos, "buys_n": len(buys), "sells_n": len(sells),
                "amt": amt, "sells_qty": len(sells) * amt,
                "pos_units": round(btc_pos / amt),
                "mid": mid,
            }))

            # 3. 错误退出
            if btc_pos < 0:
                print(f"[{cycle}] FAIL: 空头 {btc_pos}, 全撤退出")
                await signer.cancel_all_orders(time_in_force=signer.CANCEL_ALL_TIF_IMMEDIATE, timestamp_ms=0, api_key_index=ki)
                break
            if btc_pos == 0 and sells:
                print(f"[{cycle}] FAIL: 零仓+{len(sells)}卖单, 全撤退出")
                await signer.cancel_all_orders(time_in_force=signer.CANCEL_ALL_TIF_IMMEDIATE, timestamp_ms=0, api_key_index=ki)
                break

            # 4. 工作流程
            now = datetime.now().strftime("%H:%M:%S")
            print(f"\n{now} [{cycle}] mid={mid:.1f} pos={btc_pos} buys={len(buys)} sells={len(sells)}")
            await process_buys(signer, buys, sells, buy_zone, gs, ki)
            # 重查（买单撤单可能影响卖单，中间卖单成交 pos 也会变）
            btc_pos2, _, sells2 = await fetch_chain(gs, token)
            dbg.info(_json.dumps({
                "ts": datetime.now().strftime("%H:%M:%S"), "cycle": cycle, "stage": "pre_sell",
                "btc_pos2": btc_pos2, "sells2_n": len(sells2),
                "pos2_units": round(btc_pos2 / amt),
            }))
            if btc_pos2 > 0:
                await process_sells(signer, sells2, sell_zone, btc_pos2, gs, ki)

            elapsed = time.time() - t0
            print(f"  → {elapsed:.1f}s")

            # 每小时存一次净值
            now_ts = time.time()
            if now_ts - last_snapshot > 3600:
                try:
                    bal = (await fetch_chain(gs, token))[0]
                    entry = {"ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                             "total_equity": -1, "btc_pos": bal, "mid": mid}
                    # 尝试取总权益
                    async with aiohttp.ClientSession() as s:
                        async with s.get(
                            f"{REST}/api/v1/account",
                            params={"by": "index", "value": str(account_index)}
                        ) as resp:
                            acc = await resp.json()
                        entry["total_equity"] = float(
                            acc["accounts"][0].get("cross_asset_value",
                                                    acc["accounts"][0].get("total_asset_value", 0)))
                    with open("data/equity.jsonl", "a") as f:
                        f.write(_json.dumps(entry) + "\n")
                    last_snapshot = now_ts
                except Exception:
                    pass
        except SystemExit:
            now = datetime.now().strftime("%H:%M:%S")
            print(f"{now} [{cycle}] 严重错误, 退出"); break
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            print(f"{datetime.now().strftime('%H:%M:%S')} [{cycle}] 网络: {e}")
        except Exception as e:
            print(f"{datetime.now().strftime('%H:%M:%S')} [{cycle}] ERROR: {e}")
            import traceback; traceback.print_exc()

        await asyncio.sleep(10)

    print(f"退出, 共 {cycle} 轮")
    await signer.close()


asyncio.run(main())
