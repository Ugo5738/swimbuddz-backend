from services.ai_service.coach.coach import GateVerdict
from services.ai_service.pipeline.components.gate import _tier
from services.ai_service.pipeline.types import GateTier


def _verdict(
    *,
    usable: bool,
    view: str = "side-on",
    stroke: str = "freestyle",
    agreement: float = 2 / 3,
) -> GateVerdict:
    return GateVerdict(
        usable=usable,
        view=view,
        stroke=stroke,
        swimmer_count=1,
        agreement=agreement,
        n_votes=3,
        n_valid=3,
    )


def test_two_of_three_side_on_votes_is_clean():
    assert _tier(_verdict(usable=True, agreement=2 / 3)) == GateTier.CLEAN


def test_overhead_usable_clip_is_borderline_not_refused():
    assert (
        _tier(_verdict(usable=True, view="overhead", agreement=1.0))
        == GateTier.BORDERLINE
    )


def test_head_on_and_underwater_still_refuse_when_confident():
    assert (
        _tier(_verdict(usable=False, view="head-on", agreement=1.0)) == GateTier.REFUSE
    )
    assert (
        _tier(_verdict(usable=False, view="underwater", agreement=1.0))
        == GateTier.REFUSE
    )


def test_non_freestyle_still_refuses_when_confident():
    assert (
        _tier(_verdict(usable=False, stroke="other", agreement=1.0)) == GateTier.REFUSE
    )
