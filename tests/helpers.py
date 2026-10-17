"""Builders for on-chain data in the official IDL layouts, used across the tests."""
import base64
import hashlib
import struct
import time

from solter.chain import (CURVE_DISC, POOL_DISC, PUMP_AMM, PUMP_PROGRAM, TOKEN_PROGRAM, WSOL,
                          decode58, encode58)
from solter.events import AMM_BUY, AMM_SELL, EVENT_TAG, TRADE_EVENT

MINT = 'SguaCwgH7g45ZCc7SB19NadCc2XjsPDo12DMjvfpump'


def addr(n) -> str:
    return encode58(hashlib.sha256(f'address-{n}'.encode()).digest())


CREATOR = addr('creator')
FEE = addr('fee')


def key(address: str) -> bytes:
    raw = decode58(address)
    assert len(raw) == 32
    return raw


def pump_event(user, sol, *, mint=MINT, buy=True, tokens=1_000_000.0, ts=None,
               vsol=40 * 10**9, vtok=800_000_000 * 10**6, rsol=10 * 10**9,
               rtok=600_000_000 * 10**6, creator=CREATOR, full=True, quote=WSOL) -> bytes:
    ts = int(ts or time.time())
    data = (TRADE_EVENT + key(mint) + struct.pack('<QQ?', int(sol * 1e9), int(tokens * 1e6), buy)
            + key(user) + struct.pack('<qQQQQ', ts, vsol, vtok, rsol, rtok)
            + key(FEE) + struct.pack('<QQ', 95, 1000) + key(creator))
    if full:
        name = b'buy' if buy else b'sell'
        data += (struct.pack('<QQ?QQQq', 5, 10, True, 0, 0, 0, ts) + struct.pack('<I', len(name))
                 + name + b'\x00' + struct.pack('<QQQQ', 0, 0, 0, 0) + struct.pack('<I', 0)
                 + key(quote) + struct.pack('<QQQ', int(sol * 1e9), vsol, rsol)
                 + struct.pack('<QQQ', 0, 0, 0))
    return data


def amm_event(pool, user, side='buy', *, base=1000.0, sol=1.0, pool_base=200_000_000 * 10**6,
              pool_quote=85 * 10**9, ts=None, virtual_quote=0, full=True) -> bytes:
    ts = int(ts or time.time())
    data = ((AMM_BUY if side == 'buy' else AMM_SELL) + struct.pack('<q', ts)
            + struct.pack('<4Q', int(base * 1e6), 0, 0, 0) + struct.pack('<QQ', pool_base, pool_quote)
            + struct.pack('<6Q', 0, 0, 0, 0, 0, 0) + struct.pack('<Q', int(sol * 1e9))
            + key(pool) + key(user) + b''.join(key(addr(i)) for i in range(4)) + key(CREATOR))
    if full:
        data += struct.pack('<QQ', 0, 0)
        if side == 'buy':
            data += struct.pack('<?QQQqQ', False, 0, 0, 0, ts, 0) + struct.pack('<I', 3) + b'buy'
        data += struct.pack('<4Q', 0, 0, 0, 0) + virtual_quote.to_bytes(16, 'little', signed=True)
    return data


def logs(payloads, program=PUMP_PROGRAM, ix='Buy'):
    lines = ['Program ComputeBudget111111111111111111111111111111 invoke [1]',
             'Program ComputeBudget111111111111111111111111111111 success',
             f'Program {program} invoke [1]', f'Program log: Instruction: {ix}',
             f'Program {TOKEN_PROGRAM} invoke [2]', 'Program log: Instruction: Transfer',
             f'Program {TOKEN_PROGRAM} consumed 4645 of 180000 compute units',
             f'Program {TOKEN_PROGRAM} success']
    lines += ['Program data: ' + base64.b64encode(p).decode() for p in payloads]
    lines += [f'Program {program} invoke [2]', f'Program {program} consumed 2003 of 150000 compute units',
              f'Program {program} success', f'Program {program} consumed 40000 of 180000 compute units',
              f'Program {program} success']
    return lines


def curve_bytes(*, vtok=800_000_000 * 10**6, vsol=40 * 10**9, rtok=600_000_000 * 10**6,
                rsol=10 * 10**9, supply=10**15, complete=False, creator=CREATOR, mayhem=False,
                quote=WSOL) -> bytes:
    raw = bytearray(151)
    raw[:8] = CURVE_DISC
    struct.pack_into('<QQQQQ', raw, 8, vtok, vsol, rtok, rsol, supply)
    raw[48] = int(complete)
    raw[49:81] = key(creator)
    raw[81] = int(mayhem)
    raw[83:115] = key(quote)
    return bytes(raw)


def account(raw: bytes, owner=PUMP_PROGRAM) -> dict:
    return {'owner': owner, 'data': [base64.b64encode(raw).decode(), 'base64'], 'lamports': 1}


def pool_bytes(base_mint=MINT, quote_mint=WSOL, base_vault=None, quote_vault=None, coin_creator=CREATOR):
    raw = bytearray(300)
    raw[:8] = POOL_DISC
    raw[11:43] = key(addr('pool-creator'))
    raw[43:75] = key(base_mint)
    raw[75:107] = key(quote_mint)
    raw[107:139] = key(addr('lp'))
    raw[139:171] = key(base_vault or addr('base-vault'))
    raw[171:203] = key(quote_vault or addr('quote-vault'))
    raw[211:243] = key(coin_creator)
    return bytes(raw)


def pool_account(**kw) -> dict:
    return account(pool_bytes(**kw), PUMP_AMM)


def cpi_tx(payloads, program=PUMP_PROGRAM, slot=123, block_time=None):
    return {'slot': slot, 'blockTime': block_time or int(time.time()),
            'meta': {'err': None, 'logMessages': [f'Program {program} invoke [1]', f'Program {program} success'],
                     'innerInstructions': [{'index': 0, 'instructions': [
                         {'programId': program, 'accounts': [], 'data': encode58(EVENT_TAG + p),
                          'stackHeight': 2} for p in payloads]}]}}
