"""Enum definitions for corporate service models."""

import enum


def enum_values(enum_cls):
    """Return persistent DB values for SAEnum mappings."""
    return [member.value for member in enum_cls]


class CompanyIndustry(str, enum.Enum):
    """Industry classification for corporate buyers — drives ICP scoring & playbook routing."""

    TECH = "tech"
    BANK_FINANCE = "bank_finance"
    CONSULTANCY = "consultancy"
    TELCO = "telco"
    MDA_PARASTATAL = "mda_parastatal"
    FMCG = "fmcg"
    HEALTHCARE = "healthcare"
    EDUCATION = "education"
    NGO = "ngo"
    OTHER = "other"


class CompanySize(str, enum.Enum):
    """Employee-count buckets — informs deal sizing and procurement complexity."""

    UNDER_50 = "under_50"
    FROM_50_TO_250 = "50_to_250"
    FROM_250_TO_1000 = "250_to_1000"
    OVER_1000 = "over_1000"


class ContactSource(str, enum.Enum):
    """How the company entered our pipeline."""

    COLD_OUTBOUND = "cold_outbound"
    WARM_INTRO = "warm_intro"
    REFERRAL = "referral"
    INBOUND_EMAIL = "inbound_email"
    INBOUND_WEB = "inbound_web"
    EVENT = "event"
    OTHER = "other"


class DealStage(str, enum.Enum):
    """Sales pipeline stages — matches the playbook outreach sequence."""

    LEAD = "lead"  # contact created, no outreach yet
    CONTACTED = "contacted"  # email 1 sent
    INTRO_SCHEDULED = "intro_scheduled"  # 20-min call booked
    INTRO_DONE = "intro_done"  # call completed
    PROPOSAL_SENT = "proposal_sent"  # written quote out
    NEGOTIATING = "negotiating"  # pricing/terms in motion
    WON = "won"  # signed → program follows
    LOST = "lost"  # cold or declined


class DealLostReason(str, enum.Enum):
    """Why a deal closed unsuccessfully — informs ICP refinement."""

    PRICE = "price"
    TIMING = "timing"
    INTERNAL_PRIORITIES = "internal_priorities"
    BUDGET_FROZEN = "budget_frozen"
    COMPETITOR = "competitor"
    LOGISTICS = "logistics"
    NO_RESPONSE = "no_response"
    OTHER = "other"


class DiscountTier(str, enum.Enum):
    """Per-employee pricing tier — drives the total_price calculation."""

    FULL_PRICE = "full_price"  # 1-4 employees, ₦150,000 each
    BULK_5_9 = "bulk_5_9"  # 5-9 employees, ₦135,000 each (10% off)
    BULK_10_PLUS = "bulk_10_plus"  # 10+ employees, ₦127,500 each (15% off)


class PaymentTerms(str, enum.Enum):
    """Agreed payment schedule for the program."""

    FULL_UPFRONT = "full_upfront"
    DEPOSIT_HALF = "deposit_half"  # 50% deposit, 50% at week 6 (default per playbook)
    NET_30 = "net_30"
    NET_60 = "net_60"
    CUSTOM = "custom"


class ProgramStatus(str, enum.Enum):
    """Lifecycle of a sold corporate program."""

    DRAFT = "draft"  # created from a won deal, employees being added
    READY = "ready"  # employees added, cohort linked, wallet provisioned
    ACTIVE = "active"  # cohort started
    COMPLETED = "completed"  # cohort ended
    CANCELLED = "cancelled"  # called off after sale


class EmployeeEnrollmentStatus(str, enum.Enum):
    """Where each employee is in the onboarding funnel."""

    PENDING = "pending"  # on the manifest, no account yet
    INVITED = "invited"  # invitation email sent
    REGISTERED = "registered"  # member account created (member_id set)
    ENROLLED = "enrolled"  # booked into cohort sessions
    OPTED_OUT = "opted_out"  # declined / withdrew


class TouchpointType(str, enum.Enum):
    """Outreach interaction types — covers the playbook's 3-email sequence + calls."""

    EMAIL_INTRO = "email_intro"
    EMAIL_FOLLOWUP_1 = "email_followup_1"
    EMAIL_FOLLOWUP_2 = "email_followup_2"
    INTRO_CALL = "intro_call"
    PROPOSAL_SHARED = "proposal_shared"
    DEMO = "demo"
    WHATSAPP = "whatsapp"
    PHONE_CALL = "phone_call"
    IN_PERSON = "in_person"
    NOTE = "note"


class TouchpointDirection(str, enum.Enum):
    """Who initiated the touchpoint."""

    OUTBOUND = "outbound"
    INBOUND = "inbound"
