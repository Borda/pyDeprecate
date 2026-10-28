"""Keep hand-maintained documentation surfaces in step with the package and with each other."""

import json
import re
from pathlib import Path

import pytest

import deprecate
import deprecate.audit

_ROOT = Path(__file__).resolve().parents[2]
_DOCS = _ROOT / "docs"
_PUBLIC_NAMES = sorted(set(deprecate.__all__) | set(deprecate.audit.__all__))


def _code_text(text: str) -> str:
    """Return only the code of a Markdown text: fenced blocks and inline code spans, so prose words never match."""
    fenced = re.findall(r"```.*?```", text, flags=re.DOTALL)
    inline = re.findall(r"`[^`\n]+`", re.sub(r"```.*?```", "", text, flags=re.DOTALL))
    return "\n".join(fenced + inline)


def _troubleshooting_faq_names() -> list[str]:
    """Return the FAQPage question names from the troubleshooting JSON-LD block, in order."""
    html = (_DOCS / "overrides" / "main.html").read_text(encoding="utf-8")
    condition = re.search(r"""page\.file\.src_path\s*==\s*["']troubleshooting\.md["']\s*%\}""", html)
    assert condition, (
        "docs/overrides/main.html has no `page.file.src_path == troubleshooting.md` branch holding the FAQ JSON-LD"
    )
    block = html[condition.end() :]
    script_open = '<script type="application/ld+json">'
    assert script_open in block, "the troubleshooting branch of docs/overrides/main.html has no ld+json <script> block"
    script = block.split(script_open, 1)[1].split("</script>", 1)[0]
    graph = json.loads(script.replace("{{ page.canonical_url }}", ""))["@graph"]
    (faq,) = (node for node in graph if node["@type"] == "FAQPage")
    return [question["name"] for question in faq["mainEntity"]]


def _troubleshooting_headings() -> list[str]:
    """Return the H2 headings of the troubleshooting page as plain text, in order."""
    headings: list[str] = []
    fence = ""  # the opening fence marker while inside a fenced code block, else ""
    for line in (_DOCS / "troubleshooting.md").read_text(encoding="utf-8").splitlines():
        marker = re.match(r"\s*(`{3,}|~{3,})", line)
        if fence:
            if marker and marker.group(1)[0] == fence[0] and len(marker.group(1)) >= len(fence):
                fence = ""
        elif marker:
            fence = marker.group(1)
        elif line.startswith("## "):
            headings.append(line[3:].strip().replace("`", ""))
    return headings


@pytest.mark.parametrize("llms_file", ["llms.txt", "llms-full.txt"])
@pytest.mark.parametrize("name", _PUBLIC_NAMES)
def test_llms_files_name_every_public_export(llms_file: str, name: str) -> None:
    """Every name exported by ``deprecate`` or ``deprecate.audit`` is mentioned in both agent files.

    Coding agents read ``docs/llms.txt`` (and the condensed ``docs/llms-full.txt``) before generating code
    against pyDeprecate. A public name missing from either file is invisible to those agents, so they
    reinvent it or call an older spelling. The files are hand-written, so this guards against drift each
    time a name is added to an ``__all__``. A word buried in a longer code span or a code comment does not tell an
    agent the name exists, so the name needs a real anchor: its own backticked span (optionally dotted, as in
    ``deprecate.audit.name``), call or signature form (``name(``), attribute use (``name.MEMBER``), an import
    statement, or at least two mentions as code.
    """
    code = _code_text((_DOCS / llms_file).read_text(encoding="utf-8"))
    pattern = re.escape(name)
    mentions = len(re.findall(rf"\b{pattern}\b", code))
    anchors = rf"`(?:\w+\.)*{pattern}`|\b{pattern}\(|\b{pattern}\.\w|\bimport\b[^\n`]*\b{pattern}\b"
    assert re.search(anchors, code) or mentions >= 2, (
        f"`{name}` is exported but docs/{llms_file} only mentions it inside longer code ({mentions} time(s)); "
        f"give it its own backticked span, a `{name}(...)` call form, an import line, or a second mention"
    )


def test_troubleshooting_ends_with_still_stuck() -> None:
    """The "Still stuck?" pointer to the issue tracker is the last section of the troubleshooting page.

    New Q&A sections are appended over time; one landing after the closing pointer reads as an
    afterthought and hides the escalation path at the end of the page.
    """
    assert _troubleshooting_headings()[-1] == "Still stuck?"


def test_troubleshooting_faq_matches_headings() -> None:
    """The FAQ JSON-LD in ``main.html`` lists exactly the troubleshooting questions, in page order.

    Search engines show the FAQPage structured data as answers next to the page. When a Q&A section
    is added, renamed or removed on the page without the same edit in the JSON-LD, the two drift: the
    structured data advertises questions the page no longer answers, or misses new ones. Each JSON-LD
    name must equal its section heading with Markdown backticks removed.
    """
    headings = [heading for heading in _troubleshooting_headings() if heading != "Still stuck?"]
    assert _troubleshooting_faq_names() == headings
