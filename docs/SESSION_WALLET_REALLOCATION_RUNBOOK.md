# Reviewed session Bubbles reallocation

Preserve ADR-006/007/008. Attendance, entitlement and money are separate facts.
A historical draft that really occurred can be recorded as completed through the
admin Session update API, preserving its date and admitting retrospective walk-ins.
Do not publish it as a future swim or silently change a recurring Club schedule.

For a past Club swim, record its actual date/time and unpaid member/guest charges.
Use the configured visitor/guest rates; an explicit agreed member exception needs
an audit reason. A legacy Club access quote may still resolve to the normal Club
rate, so verify the member quote rather than assuming the visitor price applies.
A guest without confirmed safety consent stays waiver-missing; recording debt
never fabricates consent. Issue the existing combined session settlement link
for those recorded balances. A past ordinary booking link is not that settlement.

## Amara, reviewed 16 November 2026

Owner confirmed the 100-Bubble award was for the 3 October Community swim and
must not pay the 31 October Yaba practice. The original award metadata independently
identifies that Community swim. The Community booking already exists at NGN 10000;
the Yaba booking is NGN 5200, paid with 52 Bubbles and zero cash.

`scripts/ops/reallocate_amara_bubbles_20261116.py` is dry-run by default. It checks
exact identities, amounts, zero cash/provider exposure, original wallet metadata,
absence of competing payments, holds and promo grants, and the reviewed balance.
The maintenance transaction locks affected records, adds a 52-Bubble refund and a
100-Bubble Community purchase, links the refund, preserves the original paid
receipt with private Admin reconciliation proof, clears Yaba payment links, links
the Community settlement, and records a wallet audit. No runtime service imports
another service's models; this scoped operator maintenance uses reviewed SQL.

Expected balance: 56 + 52 - 100 = 8 Bubbles. Yaba debt becomes NGN 5200 in the
service-owned Billing projection. Original attendance and award remain intact.

The original legacy wallet debit has no journal because the old `payments` /
`session_booking` source was unmapped. The repair stages four idempotent wallet
journal payloads in the existing dead-letter table: the original award expense/liability, original Yaba recognition,
its refund, and Community event recognition. Replay them through ledger_service;
verify the original award is recognized once as NGN 10000 marketing expense, Yaba
net revenue zero, Community revenue NGN 10000, and no cash entry.
Do not mutate journal tables or relabel the original award as a new bank receipt.

Deploy the payment retry guard through feature → develop → main before applying.
Run dry-run, then `--apply --actor-label <operator>` from the exact reviewed main
script. A retry verifies the completed repair and does not debit again. If any
precondition changed, stop and review. Use `--replay-ledger` to replay only this operation's staged ledger
payloads, then verify Billing, open-payment lookup, original-payment replay safety,
wallet arithmetic, per-person charges and the ledger. Keep partial repair alerts
visible until the journal dead letters are resolved.
