# Purchased stock and Fatima pickup reconciliation — 24 November 2026

The owner approved converting the eight purchased product families to Stocked,
with five units remaining for each of thirteen purchased colour/size variants.
The owner confirmed Fatima collected her orange kickboard and black earplug
swim goggles. Her order SB-20261121-VVAUF is a pickup, historically marked Shipped;
its verified NGN 30,000 transfer and immutable financial records remain unchanged.

Use `scripts/ops/reconcile_purchased_stock_20261124.py`, dry-run by default.
The exact SKU/product manifest prevents expansion to unpaid checkouts or new SKUs.
The preparation transaction locks the reviewed rows, rejects unexpected stock,
reservations, valuation or payment state, sets Stocked sourcing, and logs each count
adjustment. Each purchased variant receives five units, plus one extra unit for
each of Fatima's two collected variants. The two extras are explicitly reserved
against her order, retaining `OrderItem.is_preorder` as the purchase-time snapshot.
Unpurchased colours/sizes keep their existing counts and availability.

After preparation commits, complete Fatima through the existing authenticated
Store admin status endpoint with `picked_up`, preserving its stock movement,
audit and normal completion pipeline. Final counts must be five on hand for all
thirteen variants, with the two Fatima reservations consumed exactly once.
The script's deterministic audit ID prevents retrying preparation from resetting
stock or resurrecting sold units. A repeated completion checks the live status and
does not send another update. A closed unexpected order or changed tender stops
for review. No refunds, new charges, receipt edits or payment replay occur.

No acquisition prices have been provided. This is an inventory-count correction,
not a supplier goods receipt or an approved opening valuation. Keep the quantities
unvalued and retain Finance's missing-cost flag until source-supported cost and
independent approval are available. Do not create zero-cost values or fake COGS.

Before applying, retain a fresh private verified database recovery point. Preview
the exact changes, then run with `--apply --actor-label` using an honest operator
label. Verify the existing paid payment, stock counts, reservation/sale movements,
completion status and unchanged original preorder snapshots after completion.
The script must first pass through feature → develop → main; execute the exact
main version. No runtime code edits, schema changes or service replacement are
needed for this scoped correction. The unmerged generic preorder-bypass draft is
not part of this repair.
