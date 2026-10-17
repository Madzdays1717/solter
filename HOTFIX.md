# Solter v1.0.1: Solana transaction v1 RPC compatibility

## Why
Solana mainnet returns RPC error -32015 when `getTransaction` is called with `maxSupportedTransactionVersion: 0` for version 1 transactions.

## Change
`solter/service.py` now sends integer `maxSupportedTransactionVersion: 1` (retaining `encoding: jsonParsed`, `commitment: confirmed`). No changes to Telegram secrets, environment, database, or signal thresholds.

## Termux install
Stop bot with Ctrl+C, then unzip this patch over the Solter v1.0.0 folder:

```sh
cd ~/solter-v1
unzip -o ~/storage/downloads/solter-v1.0.1-rpc-hotfix.zip -d .
python -m unittest discover -s tests -q
python bot.py run
```

## Alternative: immediate one-line fix
```sh
cd ~/solter-v1
sed -i "s/'maxSupportedTransactionVersion':0/'maxSupportedTransactionVersion':1/g" solter/service.py
python bot.py run
```

## What remains unverified
This offline test checks that `getTransaction` requests v1 and that existing tests pass; it does not prove live RPC parsing or subsequent call accuracy. Check `python bot.py stats` after restart.
