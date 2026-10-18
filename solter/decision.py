"""Evidence-based trade decisions built from recent, observed transactions.

This module makes no network requests. Missing wallet information is not
interpreted as proof of independent demand or proof of manipulation.
"""

from dataclasses import dataclass

from .momentum import Signal, Token


@dataclass(frozen=True)
class Decision:
    status: str
    reason: str
    signal: Signal | None
    recent_net_sol: float = 0.0
    buyer_coverage: float = 0.0
    peak_drawdown_pct: float = 0.0


class DecisionEngine:
    """Conservative about observed hazards, flexible about unavailable data."""

    @staticmethod
    def evaluate(token: Token, now: float) -> Decision:
        candidate, reason = token.decide(now)
        if candidate is None:
            rejected = reason in {
                'restricted mint controls', 'extreme buying concentration',
                'substantial recent developer selling', 'shallow pool',
            }
            return Decision('REJECT' if rejected else 'WATCH', reason, None)

        events = sorted(token.observations, key=lambda e: (e.timestamp, e.slot))
        trades = [e for e in events if e.action in ('buy', 'sell')]
        if not trades:
            return Decision('WATCH', 'belum cukup transaksi', None)

        # The latest seconds matter more than earlier trades when deciding entry.
        current = [e for e in trades if e.timestamp >= now - 25]
        prior = [e for e in trades if now - 90 <= e.timestamp < now - 45]
        recent = [e for e in trades if e.timestamp >= now - 45]
        buy = sum(e.volume_sol for e in recent if e.action == 'buy')
        sell = sum(e.volume_sol for e in recent if e.action == 'sell')
        fast_buy = sum(e.volume_sol for e in current if e.action == 'buy')
        fast_sell = sum(e.volume_sol for e in current if e.action == 'sell')
        earlier_buy = sum(e.volume_sol for e in prior if e.action == 'buy')
        net = buy - sell
        known = sum(bool(e.fills) for e in trades)
        coverage = known / len(trades)

        priced = [e for e in trades if e.price_sol > 0]
        last = priced[-1]
        peak_prices = [e.price_sol for e in priced if e.timestamp >= now - 60]
        if not peak_prices:
            return Decision('WATCH', 'menunggu harga terbaru', None, net, coverage)
        recent_peak = max(peak_prices)
        drawdown = max(0.0, (1 - last.price_sol / recent_peak) * 100)

        if drawdown >= 18 and fast_sell > fast_buy:
            return Decision('WATCH', 'harga turun dari puncak dan jual dominan',
                            None, net, coverage, drawdown)
        if fast_sell >= max(.6, fast_buy * 1.4) and fast_sell > 0:
            return Decision('WATCH', 'tekanan jual terbaru meningkat',
                            None, net, coverage, drawdown)
        if earlier_buy >= 1.0 and buy < earlier_buy * .4 and fast_buy < .35:
            return Decision('WATCH', 'arus pembelian mulai melemah',
                            None, net, coverage, drawdown)
        if (candidate.change_pct > 120 and net < .2 and
                fast_buy < .5):
            return Decision('WATCH', 'lonjakan sudah jauh; permintaan terbaru lemah',
                            None, net, coverage, drawdown)

        # Only use observed buyer concentration when sufficiently many events
        # carry trader identities. A partial feed is not a holder distribution.
        concentration = candidate.risk.buyer_concentration_pct
        if (concentration is not None and coverage >= .7 and
                candidate.risk.wallets_observed >= 4 and concentration >= 88):
            return Decision('WATCH', 'pembelian sangat terkonsentrasi',
                            None, net, coverage, drawdown)
        return Decision('CALL', 'momentum terkonfirmasi; pembelian masih aktif',
                        candidate, net, coverage, drawdown)
