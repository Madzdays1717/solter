"""Minimal dependency-free Pump.fun bonding PDA derivation and SPL mint validation.

PDA algorithm follows Solana create_program_address; seed and program ID from
https://github.com/pump-fun/pump-public-docs/blob/main/idl/pump.json
"""
from __future__ import annotations
import hashlib
import re
import base64
import struct
PUMP_PROGRAM='6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P'
TOKEN_PROGRAM='TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA'
TOKEN_2022='TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb'
ALPHABET='123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'
BASE58=re.compile(r'^[1-9A-HJ-NP-Za-km-z]{32,44}$')
P=2**255-19
D=(-121665 * pow(121666,P-2,P))%P

def decode58(s: str)->bytes:
    if not s or any(c not in ALPHABET for c in s):raise ValueError('invalid base58')
    x=0
    for c in s:x=x*58+ALPHABET.index(c)
    return b'\x00'*(len(s)-len(s.lstrip('1')))+x.to_bytes((x.bit_length()+7)//8,'big')

def encode58(b: bytes)->str:
    x=int.from_bytes(b,'big');out=''
    while x:x,r=divmod(x,58);out=ALPHABET[r]+out
    return '1'*(len(b)-len(b.lstrip(b'\x00')))+out

def valid_address(s: str)->bool:
    if not isinstance(s,str) or not BASE58.fullmatch(s):return False
    try:return len(decode58(s))==32
    except ValueError:return False

def on_curve(buf:bytes)->bool:
    if len(buf)!=32:return False
    raw=int.from_bytes(buf,'little');sign=raw>>255;y=raw&((1<<255)-1)
    if y>=P:return False
    y2=y*y%P
    x2=(y2-1)*pow((D*y2+1)%P,P-2,P)%P
    x=pow(x2,(P+3)//8,P)
    if x*x%P!=x2:x=x*pow(2,(P-1)//4,P)%P
    return x*x%P==x2 and not (x==0 and sign)

def bonding_pda(mint:str)->str:
    raw=decode58(mint)
    if len(raw)!=32:raise ValueError('invalid mint size')
    seed=b'bonding-curve'+raw
    prog=decode58(PUMP_PROGRAM)
    for bump in range(255,-1,-1):
        digest=hashlib.sha256(seed+bytes([bump])+prog+b'ProgramDerivedAddress').digest()
        if not on_curve(digest):return encode58(digest)
    raise ValueError('no PDA')

def mint_safety(account:dict|None)->tuple[bool,str]:
    if not isinstance(account,dict) or account.get('owner') not in (TOKEN_PROGRAM,TOKEN_2022):
        return False,'mint account missing or unknown program'
    data=account.get('data') or {}
    parsed=data.get('parsed') if isinstance(data,dict) else None
    if not isinstance(parsed,dict) or parsed.get('type')!='mint':
        return False,'mint not decoded'
    info=parsed.get('info') or {}
    if 'mintAuthority' not in info or 'freezeAuthority' not in info:
        return False,'mint authority unavailable'
    if info['mintAuthority'] is not None or info['freezeAuthority'] is not None:
        return False,'mint/freeze authority active'
    if account.get('owner')==TOKEN_2022:
        # Unknown extensions cannot be silently treated as vanilla tokens.
        permitted={'metadatapointer','tokenmetadata','grouppointer','tokengroup',
                   'groupmemberpointer','tokengroupmember'}
        if any(str(e.get('extension','')).lower() not in permitted
               for e in info.get('extensions',[]) if isinstance(e,dict)):
            return False,'Token-2022 transfer extension unverified'
    return True,'authorities revoked'


# BondingCurve owner, discriminator and prefix offsets from official Pump IDL.
DISC=bytes([23,183,248,55,96,216,172,96])
def curve_metadata(account:dict|None)->dict|None:
    if not isinstance(account,dict) or account.get('owner')!=PUMP_PROGRAM:return None
    data=account.get('data')
    if not isinstance(data,(list,tuple)) or len(data)<2 or data[1]!='base64':return None
    try:raw=base64.b64decode(data[0],validate=True)
    except (ValueError,TypeError):return None
    if len(raw)<115 or raw[:8]!=DISC:return None
    try:
        reserves=struct.unpack_from('<QQQQQ',raw,8)
        if any(x<=0 for x in (reserves[0],reserves[1],reserves[4])):return None
        if raw[48] not in (0,1) or raw[81] not in (0,1):return None
        return {'complete':bool(raw[48]),'creator':encode58(raw[49:81]),
                'mayhem':bool(raw[81]),'quote_mint':encode58(raw[83:115])}
    except (ValueError,struct.error):return None
