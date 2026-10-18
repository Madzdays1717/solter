"""Solana address validation."""

ALPHABET = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'
DIGITS = {letter: value for value, letter in enumerate(ALPHABET)}
SOL = 'So11111111111111111111111111111111111111112'


def valid_address(address: object) -> bool:
    if not isinstance(address, str) or not 32 <= len(address) <= 44:
        return False
    number = 0
    for letter in address:
        digit = DIGITS.get(letter)
        if digit is None:
            return False
        number = number * 58 + digit
    leading = len(address) - len(address.lstrip('1'))
    return leading + (number.bit_length() + 7) // 8 == 32
