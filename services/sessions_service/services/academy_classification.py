"""Per-learner Academy participation rules.

A session is a physical event; a learner's participation specifies whether
that event uses a purchased lesson, an additional purchase, a make-up, or an
explicit complimentary support entitlement. No fee or class balance is ever
inferred from the session title, weekday, or cohort-wide fee mode.
"""
from dataclasses import dataclass
from typing import Literal

ParticipationKind = Literal["prepaid", "paid_extra", "makeup", "support"]


@dataclass(frozen=True)
class AcademyParticipationQuote:
    kind: ParticipationKind
    fee_kobo: int
    consumes_prepaid_class: bool
    consumes_makeup_credit: bool


class AcademyParticipationError(ValueError):
    pass


def quote_participation(
    *,
    kind: ParticipationKind,
    agreed_fee_kobo: int = 0,
    purchased_classes: int,
    already_consumed: int,
    available_makeups: int = 0,
    support_authorized: bool = False,
) -> AcademyParticipationQuote:
    """Validate the per-enrollment entitlement before creating a booking.

    purchased_classes must be the enrollment's authoritative frozen quantity,
    not programme duration in weeks. already_consumed must include legacy
    attendance reconciliations and all previous prepaid class allocations.
    This pure function intentionally never invents either value.
    """
    if kind not in ("prepaid", "paid_extra", "makeup", "support"):
        raise AcademyParticipationError("Unsupported Academy participation type")
    for name, value in (
        ("agreed_fee_kobo", agreed_fee_kobo),
        ("purchased_classes", purchased_classes),
        ("already_consumed", already_consumed),
        ("available_makeups", available_makeups),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise AcademyParticipationError(f"{name} must be a nonnegative integer")
    if already_consumed > purchased_classes:
        raise AcademyParticipationError("Existing Academy class consumption exceeds entitlement")
    if kind == "prepaid":
        if agreed_fee_kobo:
            raise AcademyParticipationError("Prepaid Academy lessons cannot carry an additional fee")
        if already_consumed >= purchased_classes:
            raise AcademyParticipationError("No prepaid Academy classes remain")
        return AcademyParticipationQuote(kind, 0, True, False)
    if kind == "paid_extra":
        if agreed_fee_kobo <= 0:
            raise AcademyParticipationError("Paid extra classes require an explicitly agreed positive price")
        return AcademyParticipationQuote(kind, agreed_fee_kobo, False, False)
    if agreed_fee_kobo:
        raise AcademyParticipationError("Make-up and support classes cannot carry a separately agreed fee")
    if kind == "makeup":
        if available_makeups < 1:
            raise AcademyParticipationError("No approved make-up entitlement is available")
        return AcademyParticipationQuote(kind, 0, False, True)
    if not support_authorized:
        raise AcademyParticipationError("Complimentary support classes require explicit approval")
    return AcademyParticipationQuote(kind, 0, False, False)
