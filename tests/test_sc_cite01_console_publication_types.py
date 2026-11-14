"""SC-CITE-01 / UCD-RESEARCH-01: the Research Console offers only canonical publication types (public-only test)."""
from app import editorial as ed  # noqa: E402


def test_console_offers_exactly_the_canonical_derived_publication_types():
    """The Console never offers a type the editorial contract does not define (and never original-submission)."""
    import re
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    js = (root / "research-web/assets/research-console.js").read_text()
    offered = re.search(r"PUBLICATION_TYPES = \[([^\]]*)\]", js).group(1)
    assert [t.strip().strip('"') for t in offered.split(",")] == [t for t in ed.PUBLICATION_TYPES if t != "original-submission"]
    html = (root / "research-web/console/index.html").read_text()
    assert re.findall(r'<option value="([a-z-]+)">', html.split('id="pub-type"')[1].split("</select>")[0]) == list(ed.PUBLICATION_TYPES[:4])
