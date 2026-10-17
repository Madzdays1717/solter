"""Fair batched snapshots with same-pool, elapsed-time and send-time validation.

DexScreener rolling counters are NOT event deltas or unique wallet counts.
"""
import asyncio
import time
from .momentum import number, pick_pairs, selective_signal, confirm_progress, rank


def snapshot(pair):
    if not isinstance(pair, dict):
        return None
    pool = pair.get('pairAddress')
    price = number(pair.get('priceUsd'))
    liquidity = number((pair.get('liquidity') or {}).get('usd'))
    if not isinstance(pool, str) or not pool or not price or not liquidity:
        return None
    basis = 'mc' if number(pair.get('marketCap')) is not None else 'fdv'
    return {'pool': pool, 'price': price, 'liquidity': liquidity, 'basis': basis}


def observation(db, mint, at, reading, identity, cfg):
    row = db.db.execute('SELECT * FROM radar_samples WHERE mint=?', (mint,)).fetchone()
    old = dict(row) if row else None
    compatible = (old and identity and old['pool'] == identity['pool']
                  and old['basis'] == identity['basis'])
    # A failed/intervening observation invalidates continuity immediately,
    # even when it occurs less than confirm_seconds after the baseline.
    if (reading and compatible and old['qualified'] and
            0 <= at-old['at'] < cfg.radar_confirm_seconds):
        return None
    previous = old if (compatible and old['qualified'] and
                        cfg.radar_confirm_seconds <= at-old['at'] <= cfg.radar_max_sample_age) else None
    i = identity or {}
    db.db.execute('''INSERT INTO radar_samples(mint,at,qualified,mc,volume,buys,sells,pool,price,liquidity,basis)
        VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(mint) DO UPDATE SET
        at=excluded.at,qualified=excluded.qualified,mc=excluded.mc,volume=excluded.volume,
        buys=excluded.buys,sells=excluded.sells,pool=excluded.pool,price=excluded.price,
        liquidity=excluded.liquidity,basis=excluded.basis''',
        (mint,at,int(reading is not None and identity is not None),
         reading.market_cap_usd if reading else None, reading.volume_5m_usd if reading else None,
         reading.buys_5m if reading else None,reading.sells_5m if reading else None,
         i.get('pool'),i.get('price'),i.get('liquidity'),i.get('basis')))
    db.commit() if hasattr(db, 'commit') else db.db.commit()
    return previous


def continuity(previous, identity, cfg):
    if not previous or not identity or not previous.get('price') or not previous.get('liquidity'):
        return 'need fresh comparable samples'
    if identity['pool'] != previous['pool'] or identity['basis'] != previous['basis']:
        return 'pool or valuation basis changed'
    if identity['price'] / previous['price'] - 1 < cfg.radar_min_price_rise / 100:
        return 'price not confirming'
    if identity['liquidity'] < previous['liquidity'] * (1-cfg.radar_max_liquidity_drop/100):
        return 'liquidity declining'
    return None


def live_risk(bot, token):
    if bot.db.has_call(token['mint']):
        return 'already called'
    if token.get('creator') and 0 < bot.cfg.max_creator_launches < bot.db.launches(token['creator']):
        return 'known repeat creator'
    # Use only observed evidence, never label unknown wallets safe.
    if token.get('creator') and bot.db.db.execute(
        "SELECT 1 FROM trades WHERE mint=? AND wallet=? AND side='sell' LIMIT 1",
        (token['mint'], token['creator'])).fetchone():
        return 'observed creator sell'
    cached = bot.checked.get(token['mint'])
    if cached and cached[1].reason in {'mint/freeze authority active', 'dev holds too much',
            'dev bought too much', 'top holders concentrated', 'whale holder', 'creator sold'}:
        if bot.chain_now()-cached[0] < 120:
            return 'known risk: '+cached[1].reason
    return None


async def run_once(bot):
    cfg, db = bot.cfg, bot.db
    started = time.monotonic()
    tokens = db.radar_batch(time.time(), cfg.radar_batch, cfg.radar_minutes)
    semaphore = asyncio.Semaphore(cfg.radar_concurrency)
    async def fetch(chunk):
        async with semaphore:
            payload = await bot.dex('/tokens/v1/solana/'+','.join(t['mint'] for t in chunk))
            return chunk, payload, time.time()
    jobs = [asyncio.create_task(fetch(tokens[i:i+30])) for i in range(0,len(tokens),30)]
    candidates = []
    try:
        for task in asyncio.as_completed(jobs):
            chunk,payload,at = await task
            pairs = pick_pairs(payload,{t['mint'] for t in chunk})
            for token in chunk:
                mint = token['mint']
                pair = pairs.get(mint)
                identity = snapshot(pair)
                why = live_risk(bot,token)
                if not isinstance(payload,list):
                    why = 'HTTP unavailable'
                elif not pair:
                    why = 'pair not indexed'
                elif not identity:
                    why = 'price pool or liquidity missing'
                reading = selective_signal(pair,None,cfg) if identity and not why else None
                previous = observation(db,mint,at,reading,identity,cfg)
                db.radar_mark(mint,at,reading.market_cap_usd if reading else None,
                              reading.volume_5m_usd if reading else None)
                if not reading:
                    db.radar_status(mint,at,why or 'activity or quality thresholds')
                    continue
                why = continuity(previous,identity,cfg)
                confirmed = None if why else confirm_progress(reading,previous,
                    min_mc_rise=cfg.radar_min_confirm_mc_rise_pct,
                    min_extra_buys=cfg.radar_min_extra_buys,
                    min_extra_volume=cfg.radar_min_extra_volume_usd)
                if not confirmed:
                    db.radar_status(mint,at,why or 'rolling activity not strengthening')
                    continue
                rise,buys = confirmed
                # Normalize for sample interval; a slow refresh must not win just
                # because it accumulated larger counter changes over more time.
                seconds = max(1,at-previous['at'])
                strength = rank(reading,rise*15/seconds,buys*15/seconds)
                db.radar_status(mint,at,'ranked candidate',strength)
                candidates.append((strength,token,reading,rise,buys,identity,at))
    finally:
        for job in jobs:
            if not job.done():
                job.cancel()
        await asyncio.gather(*jobs,return_exceptions=True)
    db.metric('radar_rounds')
    db.metric('radar_scanned',len(tokens))
    db.metric('radar_round_ms',int((time.monotonic()-started)*1000))
    limit = min(cfg.radar_max_alerts_per_hour,cfg.radar_quality_max_alerts_per_hour)
    capacity = db.radar_capacity(time.time(),limit,cfg.radar_alert_gap_seconds)
    if capacity:
        for score,token,*_ in candidates:
            db.radar_status(token['mint'],time.time(),capacity,score)
        return 0
    # At most three ranked candidates revalidated; finite additional request cost.
    for strength,token,reading,rise,buys,identity,at in sorted(candidates,key=lambda x:-x[0])[:3]:
        mint = token['mint']
        if time.time()-at > cfg.radar_max_sample_age:
            db.radar_status(mint,time.time(),'candidate stale')
            continue
        payload = await bot.dex('/tokens/v1/solana/'+mint)
        pair = pick_pairs(payload,{mint}).get(mint)
        current = snapshot(pair)
        fresh = selective_signal(pair,None,cfg) if current else None
        why = live_risk(bot,db.token(mint) or token)
        if (why or not fresh or time.time()-at > cfg.radar_max_sample_age or
                current['pool']!=identity['pool'] or current['basis']!=identity['basis'] or
                current['price']<identity['price']*.99 or current['price']>identity['price']*1.05 or
                current['liquidity']<identity['liquidity']*(1-cfg.radar_max_liquidity_drop/100)):
            db.radar_status(mint,time.time(),why or 'send-time recheck failed')
            continue
        sent = await bot.send_radar(token,fresh,time.time(),rise,buys)
        db.radar_status(mint,time.time(),'alert sent' if sent else 'delivery not confirmed',strength)
        # Do not try a second token if delivery is uncertain.
        return int(sent)
    return 0
