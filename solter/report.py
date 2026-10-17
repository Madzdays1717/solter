"""What the calls actually did: hit rates, returns per horizon and grade, the funnel, misses.

Prices are SOL per token from on-chain events (DexScreener only for a
PumpSwap pool whose price the events do not give). Fees and slippage are
not included, so real returns are lower than these numbers.
"""
from __future__ import annotations

from statistics import median

from .storage import HORIZONS

PEAKS = (1.5, 2, 3, 5, 10)
FUNNEL = ('launch_seen', 'reservoir_alive', 'promoted', 'trade_from_logs', 'trade_from_cpi',
          'trade_from_balance', 'trade_from_backfill', 'migration_seen')


def _share(values: list[bool]) -> float | None:
    return sum(values) / len(values) if values else None


def _median(values: list[float]) -> float | None:
    return median(values) if values else None


def build(store, now: float, days: int = 7) -> dict:
    since = now - days * 86400
    calls = store.calls_since(since)
    tracked = [c for c in calls if c['entry']]
    peaks = [c['peak'] / c['entry'] for c in tracked]
    grades = {}
    for g in ('A', 'B', 'C'):
        group = [c for c in tracked if c['grade'] == g]
        multiples = [c['peak'] / c['entry'] for c in group]
        grades[g] = {'calls': len(group), 'hit_2x': _share([m >= 2 for m in multiples]),
                     'median_peak': _median(multiples)}
    metrics = store.metrics()
    reasons = sorted(((k.split(':', 1)[1], n) for k, n in metrics.items() if k.startswith('no_call:')),
                     key=lambda item: -item[1])
    return {
        'days': days,
        'calls': len(calls),
        'delivered': sum(1 for c in calls if c['state'] == 'sent'),
        'tracked': len(tracked),
        'finished': sum(1 for c in tracked if c['done']),
        'with_gaps': sum(1 for c in tracked if c['gap']),
        'peak_hit': {m: _share([p >= m for p in peaks]) for m in PEAKS},
        'median_peak': _median(peaks),
        'median_return': {h: _median([c[f'r{h}'] - 1 for c in tracked if c[f'r{h}'] is not None])
                          for h in HORIZONS},
        'median_drawdown': _median([c['low'] / c['entry'] - 1 for c in tracked]),
        'dev_sold_after_call': sum(1 for c in tracked if c['dev_sold']),
        'by_grade': grades,
        'funnel': {k: metrics.get(k, 0) for k in FUNNEL},
        'top_reasons': reasons[:10],
        'missed': store.missed(since, 3),
        'rpc_today': store.budget_used(),
    }


def _pct(value: float | None) -> str:
    return 'n/a' if value is None else f'{value * 100:+.0f}%'


def _rate(value: float | None) -> str:
    return 'n/a' if value is None else f'{value * 100:.0f}%'


def render(rep: dict) -> str:
    lines = [f'SOLTER REPORT · {rep["days"]} hari terakhir', '',
             f'Call: {rep["calls"]} (terkirim {rep["delivered"]}) · dilacak {rep["tracked"]} · '
             f'selesai {rep["finished"]} · ada celah data {rep["with_gaps"]}']
    if rep['tracked']:
        hits = ' · '.join(f'≥{m:g}x {_rate(v)}' for m, v in rep['peak_hit'].items())
        lines += [f'Puncak setelah call: {hits}',
                  f'Median puncak: {rep["median_peak"]:.2f}x · median drawdown {_pct(rep["median_drawdown"])}',
                  'Median return: ' + ' · '.join(f'{h}m {_pct(v)}' for h, v in rep['median_return'].items()),
                  f'Dev jual setelah call: {rep["dev_sold_after_call"]}', '', 'Per grade:']
        for g, row in rep['by_grade'].items():
            peak = f'{row["median_peak"]:.2f}x' if row['median_peak'] else 'n/a'
            lines.append(f'  {g}: {row["calls"]} call · kena 2x {_rate(row["hit_2x"])} · median puncak {peak}')
    else:
        lines.append('Belum ada call yang dilacak. Biarkan bot berjalan 24-72 jam.')
    lines += ['', 'Corong (sejak awal): ' + ' · '.join(f'{k}={v}' for k, v in rep['funnel'].items())]
    if rep['top_reasons']:
        lines += ['Alasan tidak call terbanyak:'] + [f'  {r}: {n}' for r, n in rep['top_reasons']]
    if rep['missed']:
        lines += ['', 'Token yang naik ≥3x dari pembacaan pertama tanpa call (perkiraan dari sweep):']
        for m in rep['missed']:
            lines.append(f'  {m["symbol"] or m["mint"][:8]}: {m["peak_mcap_sol"] / m["first_mcap_sol"]:.1f}x '
                         f'· status {m["state"]} · {m["reason"] or "-"}')
    lines += ['', f'RPC hari ini: {rep["rpc_today"]}',
              'Catatan: tanpa biaya/slippage. Hasil masa lalu tidak menjamin hasil berikutnya.']
    return '\n'.join(lines)
