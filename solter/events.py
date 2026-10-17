"""Trades from Pump.fun and PumpSwap events, decoded without paid APIs.

Anchor writes an event in one of two places:
- `emit!`: a `Program data: <base64>` log line. It is attributed to the
  program on top of the invoke stack. Lines are matched strictly, so text
  another program logs (`Program log: ...`) cannot impersonate an invoke.
  These logs arrive with every free `logsSubscribe` notification, so a
  trade costs no RPC request at all.
- `emit_cpi!`: a self-invocation whose data starts with the Anchor event
  tag, visible in a fetched transaction's inner instructions. Only the
  program can sign that invocation, so a successful one is authentic.

Layouts follow the official IDLs (pump.json TradeEvent, pump_amm.json
BuyEvent/SellEvent). Fields appended to an event over time are optional.
"""
from __future__ import annotations

import base64
import binascii
import re
import struct
import time
from collections import defaultdict
from dataclasses import dataclass

from .chain import (LAMPORTS, PUMP_AMM, PUMP_PROGRAM, SOL_QUOTES, TOKEN_UNIT, decode58,
                    encode58, price_sol, progress)

TRADE_EVENT = bytes([189, 219, 127, 211, 78, 230, 97, 238])
AMM_BUY = bytes([103, 244, 82, 31, 44, 245, 119, 119])
AMM_SELL = bytes([62, 47, 55, 10, 165, 3, 220, 42])
EVENT_TAG = bytes.fromhex('e445a52e51cb9a1d')   # Anchor EVENT_IX_TAG, little endian

_ADDRESS = r'[1-9A-HJ-NP-Za-km-z]{32,44}'
INVOKE = re.compile(rf'^Program ({_ADDRESS}) invoke \[\d+\]$')
EXIT = re.compile(rf'^Program ({_ADDRESS}) (?:success$|failed)')
TRADE_IX = re.compile(r'^Program log: Instruction: (?:Buy|Sell)')


@dataclass(frozen=True)
class Trade:
    wallet: str
    side: str                       # 'buy' | 'sell'
    tokens: float                   # token amount (UI units)
    sol: float | None               # SOL paid or received; None when unknown
    at: float                       # unix seconds
    price: float | None = None      # SOL per token after the trade
    progress: float | None = None   # bonding curve progress after the trade
    slot: int | None = None
    idx: int = 0                    # position among the transaction's trades
    src: str = 'log'                # 'log' | 'cpi' | 'balance'


class _Reader:
    def __init__(self, data: bytes, pos: int = 0):
        self.data = data
        self.pos = pos

    def take(self, n: int) -> bytes:
        if self.pos + n > len(self.data):
            raise ValueError('event too short')
        chunk = self.data[self.pos:self.pos + n]
        self.pos += n
        return chunk

    def u32(self) -> int:
        return struct.unpack('<I', self.take(4))[0]

    def u64(self) -> int:
        return struct.unpack('<Q', self.take(8))[0]

    def i64(self) -> int:
        return struct.unpack('<q', self.take(8))[0]

    def i128(self) -> int:
        return int.from_bytes(self.take(16), 'little', signed=True)

    def flag(self) -> bool:
        value = self.take(1)[0]
        if value > 1:
            raise ValueError('invalid bool')
        return bool(value)

    def key(self) -> str:
        return encode58(self.take(32))

    def text(self) -> str:
        n = self.u32()
        if n > 64:
            raise ValueError('string too long')
        return self.take(n).decode('utf-8', 'replace')


def decode_pump_trade(data: bytes) -> dict | None:
    if data[:8] != TRADE_EVENT:
        return None
    r = _Reader(data, 8)
    try:
        ev = {'mint': r.key(), 'sol': r.u64(), 'tokens': r.u64(), 'is_buy': r.flag(),
              'user': r.key(), 'timestamp': r.i64(), 'virtual_sol': r.u64(),
              'virtual_tokens': r.u64(), 'real_sol': r.u64(), 'real_tokens': r.u64()}
        r.key(); r.u64(); r.u64()            # fee_recipient, fee_basis_points, fee
        ev['creator'] = r.key()
    except ValueError:
        return None
    try:
        r.u64(); r.u64(); r.flag()           # creator fee bps, creator fee, track_volume
        r.u64(); r.u64(); r.u64(); r.i64()   # unclaimed, claimed, sol volume, last update
        ev['ix_name'] = r.text()
        ev['mayhem'] = r.flag()
        r.u64(); r.u64(); r.u64(); r.u64()   # cashback and buyback fees
        holders = r.u32()
        if holders > 64:
            raise ValueError('too many shareholders')
        r.take(holders * 34)                 # Shareholder {address, share_bps u16}
        ev['quote_mint'] = r.key()
        ev['quote_amount'] = r.u64()
        ev['virtual_quote'] = r.u64()
    except ValueError:
        pass                                 # older events stop before these fields
    return ev


def decode_amm_trade(data: bytes) -> dict | None:
    side = {AMM_BUY: 'buy', AMM_SELL: 'sell'}.get(bytes(data[:8]))
    if not side:
        return None
    r = _Reader(data, 8)
    try:
        ev = {'side': side, 'timestamp': r.i64(), 'base': r.u64()}
        r.u64(); r.u64(); r.u64()            # quote limit, user base/quote reserves
        ev['pool_base'] = r.u64()
        ev['pool_quote'] = r.u64()
        for _ in range(6):                   # quote amount, lp fee bps/fee, protocol fee bps/fee, quote after lp fee
            r.u64()
        ev['user_quote'] = r.u64()
        ev['pool'] = r.key()
        ev['user'] = r.key()
        r.take(32 * 4)                       # user token accounts, protocol fee recipient + account
        ev['coin_creator'] = r.key()
    except ValueError:
        return None
    try:
        r.u64(); r.u64()                     # coin creator fee bps, fee
        if side == 'buy':
            r.flag(); r.u64(); r.u64(); r.u64(); r.i64(); r.u64()
            r.text()
        r.u64(); r.u64(); r.u64(); r.u64()   # cashback and buyback fees
        ev['virtual_quote'] = r.i128()
    except ValueError:
        ev['virtual_quote'] = None
    return ev


def log_payloads(logs, program: str) -> tuple[list[bytes], bool, bool]:
    """Event payloads `program` logged, whether logs were truncated, whether it ran Buy/Sell."""
    stack: list[str] = []
    payloads: list[bytes] = []
    truncated = trade_ix = False
    for line in logs or []:
        if not isinstance(line, str):
            continue
        if line.startswith('Log truncated'):
            truncated = True
            continue
        match = INVOKE.match(line)
        if match:
            stack.append(match.group(1))
            continue
        if EXIT.match(line):
            if stack:
                stack.pop()
            continue
        if not stack or stack[-1] != program:
            continue
        if line.startswith('Program data: '):
            for part in line[len('Program data: '):].split():
                try:
                    payloads.append(base64.b64decode(part, validate=True))
                except (binascii.Error, ValueError):
                    pass
        elif TRADE_IX.match(line):
            trade_ix = True
    return payloads, truncated, trade_ix


def cpi_payloads(tx: dict, program: str) -> list[bytes]:
    meta = tx.get('meta') or {}
    payloads = []
    for group in meta.get('innerInstructions') or []:
        for ix in (group or {}).get('instructions') or []:
            if not isinstance(ix, dict) or ix.get('programId') != program:
                continue
            try:
                raw = decode58(ix.get('data') or '')
            except ValueError:
                continue
            if raw[:8] == EVENT_TAG:
                payloads.append(raw[8:])
    return payloads


def _moment(ts, fallback: float | None) -> float | None:
    now = time.time()
    for value in (ts, fallback):
        if isinstance(value, (int, float)) and 1.6e9 < value <= now + 120:
            return float(value)
    return None


def pump_trades(payloads, mint: str, *, slot=None, block_time=None, src='log') -> list[Trade]:
    trades = []
    for data in payloads:
        ev = decode_pump_trade(data)
        if not ev or ev['mint'] != mint:
            continue
        quote = ev.get('quote_mint')
        if quote is not None and quote not in SOL_QUOTES:
            continue                         # non-SOL quote curves are not supported
        at = _moment(ev['timestamp'], block_time)
        if at is None or ev['virtual_tokens'] <= 0:
            continue
        sol = ev['sol'] or ev.get('quote_amount') or 0
        virtual_sol = ev['virtual_sol'] or ev.get('virtual_quote') or 0
        trades.append(Trade(ev['user'], 'buy' if ev['is_buy'] else 'sell',
                            ev['tokens'] / TOKEN_UNIT, sol / LAMPORTS, at,
                            price_sol(virtual_sol, ev['virtual_tokens']),
                            progress(ev['real_tokens']), slot, len(trades), src))
    return trades


def amm_trades(payloads, pool: str, *, slot=None, block_time=None, src='log') -> list[Trade]:
    trades = []
    for data in payloads:
        ev = decode_amm_trade(data)
        if not ev or ev['pool'] != pool:
            continue
        at = _moment(ev['timestamp'], block_time)
        if at is None:
            continue
        # A pool with virtual quote reserves prices differently; leave its price unknown.
        price = (price_sol(ev['pool_quote'], ev['pool_base'])
                 if ev['virtual_quote'] == 0 else None)
        trades.append(Trade(ev['user'], ev['side'], ev['base'] / TOKEN_UNIT,
                            ev['user_quote'] / LAMPORTS, at, price, None, slot,
                            len(trades), src))
    return trades


def _convert(payloads, mint, account, stage, slot, block_time, src):
    if stage == 'migrated':
        return amm_trades(payloads, account, slot=slot, block_time=block_time, src=src)
    return pump_trades(payloads, mint, slot=slot, block_time=block_time, src=src)


def program_for(stage: str) -> str:
    return PUMP_AMM if stage == 'migrated' else PUMP_PROGRAM


def trades_from_logs(logs, mint: str, account: str, stage: str,
                     slot: int | None = None) -> tuple[list[Trade], bool]:
    """Trades in a log notification, and whether the transaction must be fetched instead."""
    payloads, truncated, trade_ix = log_payloads(logs, program_for(stage))
    trades = _convert(payloads, mint, account, stage, slot, None, 'log')
    return trades, not trades and (truncated or trade_ix)


def trades_from_tx(tx, mint: str, account: str, stage: str) -> list[Trade]:
    """Trades in a fetched transaction: logs, then inner instructions, then balances."""
    if not isinstance(tx, dict):
        return []
    meta = tx.get('meta') or {}
    if meta.get('err') is not None:
        return []
    program = program_for(stage)
    slot, block_time = tx.get('slot'), tx.get('blockTime')
    payloads, _, _ = log_payloads(meta.get('logMessages'), program)
    trades = _convert(payloads, mint, account, stage, slot, block_time, 'log')
    if not trades:
        trades = _convert(cpi_payloads(tx, program), mint, account, stage, slot, block_time, 'cpi')
    if not trades:
        legacy = balance_trade(tx, mint, account, program)
        trades = [legacy] if legacy else []
    return trades


def _raw_amount(entry: dict) -> int:
    try:
        return int(entry['uiTokenAmount']['amount'])
    except (KeyError, TypeError, ValueError):
        return 0


def balance_trade(tx: dict, mint: str, account: str, program: str) -> Trade | None:
    """Fallback when no event decodes: one signer's net token change, SOL estimated.

    The SOL figure is the signer's lamport change net of the fee, so it also
    holds rent and tips: an estimate, used only when events are unavailable.
    """
    meta = tx.get('meta') or {}
    if meta.get('err') is not None:
        return None
    logs = meta.get('logMessages') or []
    if not any(f'Program {program} invoke' in str(x) for x in logs):
        return None
    keys = ((tx.get('transaction') or {}).get('message') or {}).get('accountKeys') or []
    signers = {k.get('pubkey') for k in keys if isinstance(k, dict) and k.get('signer') is True}
    if not signers:
        return None

    def owners(rows):
        held = defaultdict(int)
        for row in rows or []:
            if row.get('mint') == mint and row.get('owner'):
                held[row['owner']] += _raw_amount(row)
        return held

    before, after = owners(meta.get('preTokenBalances')), owners(meta.get('postTokenBalances'))
    deltas = {w: after.get(w, 0) - before.get(w, 0) for w in set(before) | set(after)}
    matched = [(w, d) for w, d in deltas.items() if w in signers and w != account and d != 0]
    if len(matched) != 1:
        return None                          # multi-signer or multi-trade: ambiguous
    wallet, delta = matched[0]
    at = _moment(None, tx.get('blockTime'))
    if at is None:
        return None
    sol = None
    index = next((i for i, k in enumerate(keys) if isinstance(k, dict) and k.get('pubkey') == wallet), None)
    pre, post = meta.get('preBalances') or [], meta.get('postBalances') or []
    if index is not None and index < len(pre) and index < len(post):
        fee = (meta.get('fee') or 0) if index == 0 else 0   # account 0 pays the fee
        change = post[index] - pre[index] + fee
        sol = abs(change) / LAMPORTS
    return Trade(wallet, 'buy' if delta > 0 else 'sell', abs(delta) / TOKEN_UNIT, sol, at,
                 None, None, tx.get('slot'), 0, 'balance')
