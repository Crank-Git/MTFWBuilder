"""Tests for the example nginx config of the production overlay (#28)."""

import re
from pathlib import Path

CONFIG = Path(__file__).resolve().parent.parent / "nginx" / "nginx.conf.example"
SECURITY_HEADERS = ("Strict-Transport-Security", "X-Frame-Options", "X-Content-Type-Options", "Referrer-Policy")


def _blocks(text: str) -> list[str]:
    """Return the direct directives of each `{ ... }` block, without the text of its child blocks."""
    stack: list[list[str]] = [[]]
    blocks: list[str] = []
    for char in re.sub(r"#[^\n]*", "", text):
        if char == "{":
            stack.append([])
        elif char == "}":
            blocks.append("".join(stack.pop()))
        else:
            stack[-1].append(char)
    return blocks


def test_each_block_with_add_header_sends_all_security_headers():
    # nginx drops the inherited add_header lines in a block that has its own add_header.
    blocks = [block for block in _blocks(CONFIG.read_text()) if "add_header" in block]
    assert blocks
    for block in blocks:
        for header in SECURITY_HEADERS:
            assert re.search(rf"add_header\s+{header}\s.*\balways;", block), header


def test_config_sends_no_x_xss_protection_header():
    assert "X-XSS-Protection" not in CONFIG.read_text()


def test_request_body_limit_is_one_megabyte():
    assert re.findall(r"client_max_body_size\s+(\S+);", CONFIG.read_text()) == ["1M"]
