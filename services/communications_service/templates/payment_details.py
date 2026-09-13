"""Receipt labels for the frozen product quote; never infer Bubbles from cash."""


def checkout_details(quote: dict | None, currency: str = "NGN") -> dict[str, str]:
    if not quote:
        return {}

    def money(value):
        return f"{currency} {int(value or 0) / 100:,.2f}"

    details = {"Original price": money(quote.get("subtotal_kobo"))}
    if quote.get("discount_kobo"):
        details["Discount"] = f"−{money(quote['discount_kobo'])}"
    if quote.get("bubbles_to_apply"):
        details["Paid with Bubbles"] = str(int(quote["bubbles_to_apply"]))
    details["Paid in cash"] = money(quote.get("total_kobo"))
    if quote.get("additional_charges_total_kobo"):
        details["Processing charges (included in cash)"] = money(
            quote["additional_charges_total_kobo"]
        )
    return details
