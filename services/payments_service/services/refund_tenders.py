"""Translate Academy's contractual credit into the tender actually paid.

Promotional credit is not refundable money. Processing fees and a separately
purchased Membership are not Academy tuition. Preserve fractional wallet value
for explicit reconciliation rather than silently cashing out or dropping it.
"""

from fastapi import HTTPException

from libs.common.currency import KOBO_PER_BUBBLE


def academy_refund_tenders(metadata: dict, gross_refund_kobo: int) -> dict:
    quote = metadata.get("checkout_quote")
    if not quote:
        return {"refund_kobo": gross_refund_kobo}
    net = int((quote.get("net_components_kobo") or {}).get("academy_cohort") or 0)
    discount = int(
        (quote.get("discount_allocations_kobo") or {}).get("academy_cohort") or 0
    )
    gross = net + discount
    if not 0 <= gross_refund_kobo <= gross:
        raise HTTPException(
            409, "Refund exceeds the Academy amount credited by this payment"
        )
    refundable = net * gross_refund_kobo // gross if gross else 0
    total_net = int(quote["net_subtotal_kobo"])
    wallet = int(quote["bubbles_value_kobo"])
    wallet_refund = refundable * wallet // total_net if total_net else 0
    return {
        "gross_refund_kobo": gross_refund_kobo,
        "discount_excluded_kobo": gross_refund_kobo - refundable,
        "refund_kobo": refundable - wallet_refund,  # cash ONLY
        "refund_bubbles": wallet_refund // KOBO_PER_BUBBLE,
        "refund_bubbles_remainder_kobo": wallet_refund % KOBO_PER_BUBBLE,
        "net_refund_kobo": refundable,
    }
