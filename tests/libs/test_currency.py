from libs.common.currency import (
    KOBO_PER_BUBBLE,
    bubbles_to_kobo,
    bubbles_to_naira,
    kobo_to_bubbles,
    naira_to_bubbles,
)


def test_bubble_value_contract():
    assert bubbles_to_naira(1) == 100
    assert bubbles_to_naira(6) == 600
    assert bubbles_to_kobo(1) == KOBO_PER_BUBBLE == 10_000


def test_naira_and_kobo_to_bubbles_floor_to_whole_bubbles():
    assert naira_to_bubbles(3_500) == 35
    assert kobo_to_bubbles(350_000) == 35
    assert naira_to_bubbles(3_499) == 34
