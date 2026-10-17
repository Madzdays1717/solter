"""Solana primitives without an SDK: base58, PDAs, account decoding and curve math.

Program IDs, PDA seeds and account layouts follow the official Pump IDLs:
https://github.com/pump-fun/pump-public-docs/tree/main/idl (pump.json, pump_amm.json)
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import re
import struct
from dataclasses import dataclass

PUMP_PROGRAM = '6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P'
PUMP_AMM = 'pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA'
TOKEN_PROGRAM = 'TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA'
TOKEN_2022 = 'TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb'
ATA_PROGRAM = 'ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL'
WSOL = 'So11111111111111111111111111111111111111112'
SYSTEM = '11111111111111111111111111111111'
# A curve whose quote mint is WSOL, or zeroed (older layouts), trades against SOL.
SOL_QUOTES = frozenset({WSOL, SYSTEM})

LAMPORTS = 10**9
TOKEN_UNIT = 10**6                      # Pump.fun tokens have 6 decimals
PUMP_SUPPLY = 1_000_000_000             # tokens, fixed for Pump.fun launches
INITIAL_REAL_TOKENS = 793_100_000 * TOKEN_UNIT   # Global.initial_real_token_reserves

ALPHABET = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'
_INDEX = {c: i for i, c in enumerate(ALPHABET)}
BASE58 = re.compile(r'^[1-9A-HJ-NP-Za-km-z]{32,44}$')
_P = 2**255 - 19
_D = (-121665 * pow(121666, _P - 2, _P)) % _P


def decode58(text: str) -> bytes:
    if not text or any(c not in _INDEX for c in text):
        raise ValueError('invalid base58')
    value = 0
    for c in text:
        value = value * 58 + _INDEX[c]
    zeros = len(text) - len(text.lstrip('1'))
    return b'\x00' * zeros + value.to_bytes((value.bit_length() + 7) // 8, 'big')


def encode58(raw: bytes) -> str:
    value = int.from_bytes(raw, 'big')
    out = ''
    while value:
        value, rest = divmod(value, 58)
        out = ALPHABET[rest] + out
    return '1' * (len(raw) - len(raw.lstrip(b'\x00'))) + out


def key_bytes(address: str) -> bytes:
    raw = decode58(address)
    if len(raw) != 32:
        raise ValueError('address is not 32 bytes')
    return raw


def valid_address(address) -> bool:
    if not isinstance(address, str) or not BASE58.fullmatch(address):
        return False
    try:
        key_bytes(address)
    except ValueError:
        return False
    return True


def on_curve(buf: bytes) -> bool:
    """Whether 32 bytes decode to an ed25519 point (a PDA must not)."""
    if len(buf) != 32:
        return False
    raw = int.from_bytes(buf, 'little')
    sign = raw >> 255
    y = raw & ((1 << 255) - 1)
    if y >= _P:
        return False
    y2 = y * y % _P
    x2 = (y2 - 1) * pow((_D * y2 + 1) % _P, _P - 2, _P) % _P
    x = pow(x2, (_P + 3) // 8, _P)
    if x * x % _P != x2:
        x = x * pow(2, (_P - 1) // 4, _P) % _P
    return x * x % _P == x2 and not (x == 0 and sign)


def find_pda(seeds: list[bytes], program: str) -> str:
    """Solana find_program_address: the highest bump whose hash is off the curve."""
    prog = key_bytes(program)
    base = b''.join(seeds)
    for bump in range(255, -1, -1):
        digest = hashlib.sha256(base + bytes([bump]) + prog + b'ProgramDerivedAddress').digest()
        if not on_curve(digest):
            return encode58(digest)
    raise ValueError('no PDA')


def bonding_curve(mint: str) -> str:
    return find_pda([b'bonding-curve', key_bytes(mint)], PUMP_PROGRAM)


def associated_account(owner: str, mint: str, token_program: str = TOKEN_PROGRAM) -> str:
    return find_pda([key_bytes(owner), key_bytes(token_program), key_bytes(mint)], ATA_PROGRAM)


def migration_pool(mint: str) -> str:
    """The canonical PumpSwap pool Pump's `migrate` instruction creates for a mint."""
    raw = key_bytes(mint)
    authority = find_pda([b'pool-authority', raw], PUMP_PROGRAM)
    return find_pda([b'pool', b'\x00\x00', key_bytes(authority), raw, key_bytes(WSOL)], PUMP_AMM)


def account_data(account: dict | None, owner: str) -> bytes | None:
    """Raw bytes of an RPC account (base64 encoding) owned by `owner`."""
    if not isinstance(account, dict) or account.get('owner') != owner:
        return None
    data = account.get('data')
    if not isinstance(data, (list, tuple)) or len(data) < 2 or data[1] != 'base64':
        return None
    try:
        return base64.b64decode(data[0], validate=True)
    except (binascii.Error, ValueError, TypeError):
        return None


def mint_safety(account: dict | None) -> tuple[bool, str]:
    """Mint and freeze authority must be revoked; unknown Token-2022 extensions fail closed."""
    if not isinstance(account, dict) or account.get('owner') not in (TOKEN_PROGRAM, TOKEN_2022):
        return False, 'mint account missing or unknown program'
    data = account.get('data') or {}
    parsed = data.get('parsed') if isinstance(data, dict) else None
    if not isinstance(parsed, dict) or parsed.get('type') != 'mint':
        return False, 'mint not decoded'
    info = parsed.get('info') or {}
    if 'mintAuthority' not in info or 'freezeAuthority' not in info:
        return False, 'mint authority unavailable'
    if info['mintAuthority'] is not None or info['freezeAuthority'] is not None:
        return False, 'mint/freeze authority active'
    if account.get('owner') == TOKEN_2022:
        permitted = {'metadatapointer', 'tokenmetadata', 'grouppointer', 'tokengroup',
                     'groupmemberpointer', 'tokengroupmember'}
        if any(str(e.get('extension', '')).lower() not in permitted
               for e in info.get('extensions', []) if isinstance(e, dict)):
            return False, 'Token-2022 transfer extension unverified'
    return True, 'authorities revoked'


def mint_supply(account: dict | None) -> int | None:
    try:
        return int(account['data']['parsed']['info']['supply'])
    except (KeyError, TypeError, ValueError):
        return None


def price_sol(virtual_sol: int, virtual_tokens: int) -> float | None:
    """SOL per token from raw reserves (SOL 9 decimals, token 6 decimals)."""
    if virtual_sol <= 0 or virtual_tokens <= 0:
        return None
    return (virtual_sol / LAMPORTS) / (virtual_tokens / TOKEN_UNIT)


def progress(real_tokens: int) -> float:
    """Share of the bonding curve's sellable tokens already bought (graduation at 1.0)."""
    return min(1.0, max(0.0, 1 - real_tokens / INITIAL_REAL_TOKENS))


CURVE_DISC = bytes([23, 183, 248, 55, 96, 216, 172, 96])
CURVE_BYTES = 115   # discriminator through quote_mint; enough for every field used here


@dataclass(frozen=True)
class Curve:
    virtual_tokens: int
    virtual_sol: int
    real_tokens: int
    real_sol: int
    supply: int
    complete: bool
    creator: str
    mayhem: bool
    quote_mint: str

    @property
    def sol_quoted(self) -> bool:
        return self.quote_mint in SOL_QUOTES

    @property
    def price(self) -> float | None:
        return price_sol(self.virtual_sol, self.virtual_tokens)

    @property
    def mcap_sol(self) -> float | None:
        p = self.price
        return p * self.supply / TOKEN_UNIT if p else None

    @property
    def progress(self) -> float:
        return 1.0 if self.complete else progress(self.real_tokens)


def decode_curve(raw: bytes | None) -> Curve | None:
    if not raw or len(raw) < CURVE_BYTES or raw[:8] != CURVE_DISC:
        return None
    try:
        vt, vs, rt, rs, supply = struct.unpack_from('<QQQQQ', raw, 8)
    except struct.error:
        return None
    if vt <= 0 or vs <= 0 or supply <= 0 or raw[48] > 1 or raw[81] > 1:
        return None
    return Curve(vt, vs, rt, rs, supply, bool(raw[48]), encode58(raw[49:81]), bool(raw[81]),
                 encode58(raw[83:115]))


def curve_account(account: dict | None) -> Curve | None:
    return decode_curve(account_data(account, PUMP_PROGRAM))


POOL_DISC = bytes([241, 154, 109, 4, 17, 177, 109, 188])


@dataclass(frozen=True)
class Pool:
    base_mint: str
    quote_mint: str
    base_vault: str
    quote_vault: str
    coin_creator: str


def pool_account(account: dict | None) -> Pool | None:
    raw = account_data(account, PUMP_AMM)
    if not raw or len(raw) < 243 or raw[:8] != POOL_DISC:
        return None
    # pool_bump u8, index u16, creator, base_mint, quote_mint, lp_mint, base vault, quote vault,
    # lp_supply u64, coin_creator (pump_amm.json Pool)
    return Pool(encode58(raw[43:75]), encode58(raw[75:107]), encode58(raw[139:171]),
                encode58(raw[171:203]), encode58(raw[211:243]))
