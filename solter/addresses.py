"""Small Solana address helpers with no blockchain RPC dependency."""

ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
DIGITS = {char: value for value, char in enumerate(ALPHABET)}
WSOL = "So11111111111111111111111111111111111111112"
USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
USDT = "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"
QUOTE_MINTS = frozenset((WSOL, USDC, USDT))


def valid_mint(address: object) -> bool:
    """Validate base58 encoding and 32 decoded bytes; no network calls."""
    if not isinstance(address, str) or not 32 <= len(address) <= 44:
        return False
    value = 0
    for char in address:
        digit = DIGITS.get(char)
        if digit is None:
            return False
        value = value * 58 + digit
    zero_bytes = len(address) - len(address.lstrip('1'))
    decoded_bytes = (value.bit_length() + 7) // 8
    return zero_bytes + decoded_bytes == 32
