"""Tests for the rendered pages: vendored assets, page titles, theme script and logs."""

import re
from html.parser import HTMLParser
from urllib.parse import urlsplit

import pytest
from httpx import ASGITransport, AsyncClient

from tests.conftest import REPO_ROOT

# The client sends requests to this host, so `url_for` builds URLs on it.
OWN_HOST = "test"

PAGES = ["/", "/admin"]


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url=f"http://{OWN_HOST}") as c:
        yield c


class _AssetParser(HTMLParser):
    """Collect the asset URLs, the title and the order of the head elements."""

    def __init__(self):
        super().__init__()
        self.assets = []
        self.title = ""
        self.head_items = []
        self._in_head = False
        self._in_title = False
        self._in_script = False
        self._script_text = ""

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "head":
            self._in_head = True
        elif tag == "title":
            self._in_title = True
        elif tag == "script":
            if attrs.get("src"):
                self.assets.append(attrs["src"])
            self._in_script = True
            self._script_text = ""
        elif tag == "link" and attrs.get("href"):
            self.assets.append(attrs["href"])
            if self._in_head:
                self.head_items.append(("link", attrs["href"]))

    def handle_endtag(self, tag):
        if tag == "head":
            self._in_head = False
        elif tag == "title":
            self._in_title = False
        elif tag == "script":
            if self._in_head:
                self.head_items.append(("script", self._script_text))
            self._in_script = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        if self._in_script:
            self._script_text += data


async def _parse(client, path):
    resp = await client.get(path)
    assert resp.status_code == 200
    parser = _AssetParser()
    parser.feed(resp.text)
    return parser


@pytest.mark.asyncio
@pytest.mark.parametrize("path", PAGES)
async def test_page_loads_no_script_or_stylesheet_from_another_host(client, path):
    parser = await _parse(client, path)
    assert parser.assets
    off_host = [url for url in parser.assets if urlsplit(url).netloc not in ("", OWN_HOST)]
    assert off_host == []


@pytest.mark.asyncio
@pytest.mark.parametrize("path", PAGES)
async def test_page_loads_htmx_sse_and_bootstrap_from_static_vendor(client, path):
    parser = await _parse(client, path)
    paths = [urlsplit(url).path for url in parser.assets]
    for name in ("htmx-2.0.", "htmx-ext-sse-2.2.", "bootstrap-5.3.", "bootstrap-icons-1."):
        assert any(p.startswith("/static/vendor/") and name in p for p in paths), name


@pytest.mark.asyncio
@pytest.mark.parametrize("path", PAGES)
async def test_every_asset_of_a_page_returns_200(client, path):
    parser = await _parse(client, path)
    for url in parser.assets:
        resp = await client.get(urlsplit(url).path)
        assert resp.status_code == 200, url


def test_icon_font_files_that_the_icon_stylesheet_names_exist():
    css_files = list((REPO_ROOT / "static" / "vendor").glob("bootstrap-icons-*/bootstrap-icons.min.css"))
    assert len(css_files) == 1
    css = css_files[0]
    fonts = re.findall(r'url\("([^"?]+)', css.read_text())
    assert fonts
    for font in fonts:
        assert (css.parent / font).is_file(), font


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "title"),
    [("/", "Build — MTFWBuilder"), ("/admin", "Admin — MTFWBuilder")],
)
async def test_page_title_names_the_page(client, path, title):
    parser = await _parse(client, path)
    assert parser.title.strip() == title


@pytest.mark.asyncio
@pytest.mark.parametrize("path", PAGES)
async def test_theme_script_runs_in_head_before_the_first_stylesheet(client, path):
    parser = await _parse(client, path)
    kinds = [kind for kind, _ in parser.head_items]
    assert "script" in kinds
    first_script = kinds.index("script")
    assert "data-bs-theme" in parser.head_items[first_script][1]
    assert first_script < kinds.index("link")


def test_pages_write_no_console_log_of_a_value():
    # A value after the first string, or a value in place of it, can be the form data.
    value_log = re.compile(r"console\.log\((?:'[^']*'|\"[^\"]*\"|`[^`]*`)\s*,|console\.log\(\s*[A-Za-z_$]")
    sources = [*(REPO_ROOT / "templates").rglob("*.html"), *(REPO_ROOT / "static" / "js").rglob("*.js")]
    offenders = [
        f"{src.relative_to(REPO_ROOT)}:{number}"
        for src in sources
        for number, line in enumerate(src.read_text().splitlines(), start=1)
        if value_log.search(line)
    ]
    assert offenders == []
