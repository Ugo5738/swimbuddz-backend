from pathlib import Path
from html.parser import HTMLParser


def test_signup_confirmation_uses_inline_white_label_and_supabase_url():
    class Buttons(HTMLParser):
        def handle_starttag(self, tag, attrs):
            attributes = dict(attrs)
            if tag == "a":
                assert attributes["href"] == "{{ .ConfirmationURL }}"
                assert "color: #ffffff !important" in attributes["style"]
            if tag == "span":
                assert "color: #ffffff !important" in attributes["style"]

    source = (Path(__file__).parents[2] / "docs/auth/confirm-signup.html").read_text()
    assert "Confirm Email Address" in source
    Buttons().feed(source)
