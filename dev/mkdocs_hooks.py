"""MkDocs hooks for AI-agent discoverability.

Injects a ``<link>`` element pointing to ``/llms.txt`` into every page's ``<head>`` so that AI agents can discover the
structured documentation index.

"""

from __future__ import annotations

import os
import re
import shutil as _pydeprecate_shutil
from pathlib import Path as _PyDeprecatePath
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mkdocs.config.defaults import MkDocsConfig
    from mkdocs.structure.pages import Page

# Module-level cache for the MkDocs config, populated in on_config so URL helpers
# can derive the public root from config.extra.root_site_url instead of a hard-coded constant.
_pydeprecate_config: MkDocsConfig | None = None


def on_config(config: MkDocsConfig, **_kwargs: object) -> MkDocsConfig:
    """Inject package version and root site URL into extra config so templates can reference them."""
    global _pydeprecate_config
    _pydeprecate_config = config
    try:
        about = os.path.join(os.path.dirname(os.path.dirname(__file__)), "src", "deprecate", "__about__.py")
        with open(about) as f:
            match = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', f.read())
        config.extra["package_version"] = match.group(1) if match else ""
    except Exception:
        config.extra["package_version"] = ""
    # mike overrides site_url with a versioned path during builds; strip the trailing
    # version/alias segment so templates can reference the stable root URL.
    raw = str(getattr(config, "site_url", "") or "").rstrip("/")
    root = re.sub(r"/(?:v?\d[\w.]*|stable|dev|latest)$", "", raw)
    config.extra["root_site_url"] = root + "/"
    return config


# PYDEPRECATE_GEO_SEO_HOOKS
# Search and agent discovery helpers. Kept in hooks so generated metadata stays
# aligned with mike aliases and rendered page URLs.

_PYDEPRECATE_PUBLIC_ROOT = "https://borda.github.io/pyDeprecate"
_PYDEPRECATE_STABLE_BASE = f"{_PYDEPRECATE_PUBLIC_ROOT}/stable/"
_PYDEPRECATE_SEARCH_DIALOG = '<div class="md-search" data-md-component="search" role="dialog">'
_PYDEPRECATE_SEARCH_DIALOG_LABELLED = (
    '<div class="md-search" data-md-component="search" role="dialog" aria-label="Search">'
)
_PYDEPRECATE_VERSION_BUTTON_RE = re.compile(
    r'(<button class="md-version__current" aria-label="Select version">)([^<]+)(</button>)'
)


def _pydeprecate_stable_base() -> str:
    """Return the stable base URL, derived from ``config.extra.root_site_url`` when available.

    Falls back to the hard-coded ``_PYDEPRECATE_STABLE_BASE`` constant so local previews and forks that configure a
    different ``site_url`` still produce correct canonical URLs.

    """
    if _pydeprecate_config is not None:
        root = _pydeprecate_config.extra.get("root_site_url")
        if isinstance(root, str):
            root = root.rstrip("/")
            if root:
                return f"{root}/stable/"
    return _PYDEPRECATE_STABLE_BASE


def _pydeprecate_root_url() -> str:
    """Return the root site URL (no version suffix), derived from config when available."""
    if _pydeprecate_config is not None:
        root = _pydeprecate_config.extra.get("root_site_url")
        if isinstance(root, str):
            root = root.rstrip("/")
            if root:
                return root
    return _PYDEPRECATE_PUBLIC_ROOT


def _pydeprecate_public_url(page: Page) -> str:
    base = _pydeprecate_stable_base()
    page_url = getattr(page, "url", "") or ""
    if page_url in {"", "index.html"}:
        return base
    return f"{base}{page_url}"


def _pydeprecate_label_search_dialog(output: str) -> str:
    return output.replace(_PYDEPRECATE_SEARCH_DIALOG, _PYDEPRECATE_SEARCH_DIALOG_LABELLED)


def _pydeprecate_label_version_button(output: str) -> str:
    return _PYDEPRECATE_VERSION_BUTTON_RE.sub(
        r'<button class="md-version__current" aria-label="Select version \2">\2\3',
        output,
    )


def _pydeprecate_fix_accessibility(output: str) -> str:
    return _pydeprecate_label_version_button(_pydeprecate_label_search_dialog(output))


def _pydeprecate_markdown_url(page: Page) -> str:
    base = _pydeprecate_stable_base()
    page_url = getattr(page, "url", "") or "index.html"
    # Directory-style URLs (use_directory_urls=True) end with '/'; strip so the
    # resulting path matches the .html.md mirror target produced by _pydeprecate_mirror_target.
    if page_url.endswith("/"):
        page_url = page_url.rstrip("/") + ".html"
    if not page_url:
        page_url = "index.html"
    return f"{base}{page_url}.md"


_PYDEPRECATE_FAQ_PAGE = "troubleshooting.md"
_PYDEPRECATE_FAQ_CLOSING_HEADING = "Still stuck?"
_PYDEPRECATE_FAQ_SKIPPED_PARAGRAPH = ("**Q:**", "!!!", "<", "|", ">", "-")


def _pydeprecate_plain_text(markdown: str) -> str:
    """Return Markdown inline text as plain text: links keep their label, bold markers go, code spans keep their
    text."""

    def _keep(match: re.Match[str]) -> str:
        code, label = match.group(1), match.group(2)
        return code if code is not None else (label or "")

    return re.sub(r"`([^`]*)`|\[([^\]]+)\]\([^)]*\)|\*\*", _keep, markdown).strip()


def _pydeprecate_h2_paragraphs(markdown: str) -> list[tuple[str, list[str]]]:
    """Split Markdown into ``(H2 heading, prose paragraphs)`` pairs, skipping fenced code."""
    sections: list[tuple[str, list[str]]] = []
    fence = ""
    buffer: list[str] = []
    for line in [*markdown.splitlines(), ""]:
        marker = re.match(r"\s*(`{3,}|~{3,})", line)
        if fence:
            if marker and marker.group(1)[0] == fence[0] and len(marker.group(1)) >= len(fence):
                fence = ""
            continue
        if marker:
            fence, line = marker.group(1), ""
        heading = line.startswith("## ")
        if buffer and (heading or not line.strip()):
            if sections:
                sections[-1][1].append(" ".join(buffer))
            buffer = []
        if heading:
            sections.append((line[3:].strip(), []))
        elif line.strip() and sections:
            buffer.append(line.strip())
    return sections


def _pydeprecate_faq_entries(markdown: str) -> list[dict[str, str]]:
    """Build the FAQPage questions from the troubleshooting page: each H2 is a question, its first prose paragraph the
    answer."""
    entries: list[dict[str, str]] = []
    for heading, paragraphs in _pydeprecate_h2_paragraphs(markdown):
        if heading == _PYDEPRECATE_FAQ_CLOSING_HEADING:
            continue
        prose = [
            para
            for para in paragraphs
            if not para.startswith(_PYDEPRECATE_FAQ_SKIPPED_PARAGRAPH)
            and not re.fullmatch(r"[-_*\s]{3,}", para)
            and not re.fullmatch(r"\*\*[^*]+\*\*", para)
        ]
        if not prose:
            raise ValueError(f"{_PYDEPRECATE_FAQ_PAGE}: section {heading!r} has no answer paragraph for the FAQ JSON-LD")
        answer = re.sub(r"^\*\*A:\*\*\s*", "", prose[0])
        if answer.endswith(":"):  # a lead-in to code or a list: continue with the next prose paragraph, if any
            answer = f"{answer} {prose[1]}" if len(prose) > 1 else answer.rstrip(":")
        entries.append({"name": heading.replace("`", ""), "text": _pydeprecate_plain_text(answer)})
    return entries


def on_page_context(
    context: dict[str, object], page: Page, config: MkDocsConfig, nav: object
) -> dict[str, object]:
    """Inject canonical URLs, and the FAQ questions of the troubleshooting page, into the page context."""
    canonical = _pydeprecate_public_url(page)
    context["pydeprecate_canonical_url"] = canonical
    page.canonical_url = canonical
    if page.file.src_path == _PYDEPRECATE_FAQ_PAGE:
        context["pydeprecate_faq"] = _pydeprecate_faq_entries(page.markdown)
    return context


def on_post_page(output: str, page: Page, config: MkDocsConfig) -> str:
    """Inject metadata into every rendered page.

    Injects canonical URLs, page-specific markdown mirror links, and llms.txt discovery link into the page <head>. JSON-
    LD structured data is handled entirely by the Jinja2 template.

    """
    canonical = _pydeprecate_public_url(page)
    markdown = _pydeprecate_markdown_url(page)
    output = re.sub(
        r'<link rel="canonical" href="[^"]+">',
        f'<link rel="canonical" href="{canonical}">',
        output,
    )
    output = re.sub(
        r'<meta property="og:url" content="[^"]+">',
        f'<meta property="og:url" content="{canonical}">',
        output,
    )
    # Accessibility fixes are applied in on_post_build() for all generated HTML outputs.
    # Replace any existing text/markdown alternate link with the page-specific mirror URL.
    # This may be set by MkDocs or a prior hook; we overwrite to ensure it points to the
    # correct versioned mirror, not the root llms.txt.
    markdown_tag = f'<link rel="alternate" type="text/markdown" href="{markdown}">'
    replaced = re.sub(
        r'<link rel="alternate" type="text/markdown" href="[^"]+">',
        markdown_tag,
        output,
    )
    # Tag was absent — insert it so the mirror URL is always discoverable.
    output = output.replace("</head>", f"{markdown_tag}\n</head>", 1) if replaced == output else replaced
    # Inject the root llms.txt discovery link (type=text/plain) separately — distinct
    # from the per-page markdown mirror link above so both survive in <head>.
    llms_url = f"{_pydeprecate_root_url()}/llms.txt"
    llms_href = f'href="{llms_url}"'
    if llms_href not in output:
        output = output.replace(
            "</head>",
            f'<link rel="alternate" type="text/plain" title="llms.txt" href="{llms_url}">\n</head>',
            1,
        )
    return output


def _pydeprecate_mirror_target(src_path: str) -> str:
    if src_path == "index.md":
        return "index.html.md"
    return src_path.removesuffix(".md") + ".html.md"


def _pydeprecate_copy_markdown_mirrors(docs_dir: str, site_dir: str) -> None:
    docs = _PyDeprecatePath(docs_dir)
    site = _PyDeprecatePath(site_dir)
    for src in docs.rglob("*.md"):
        if "overrides" in src.relative_to(docs).parts:
            continue
        src_name = src.relative_to(docs).as_posix()
        dest = site / _pydeprecate_mirror_target(src_name)
        dest.parent.mkdir(parents=True, exist_ok=True)
        _pydeprecate_shutil.copyfile(src, dest)


def _pydeprecate_rewrite_sitemap(site_dir: str) -> None:
    sitemap = _PyDeprecatePath(site_dir) / "sitemap.xml"
    if not sitemap.exists():
        return
    text = sitemap.read_text(encoding="utf-8")
    text = re.sub(
        rf"{re.escape(_pydeprecate_root_url())}/(?:v?[^/]+|latest|dev)/",
        _pydeprecate_stable_base(),
        text,
    )
    sitemap.write_text(text, encoding="utf-8")


def on_post_build(config: MkDocsConfig) -> None:
    """Copy markdown mirrors and rewrite sitemap after build completes."""
    _pydeprecate_copy_markdown_mirrors(config["docs_dir"], config["site_dir"])
    _pydeprecate_rewrite_sitemap(config["site_dir"])
    for path in _PyDeprecatePath(config["site_dir"]).rglob("*.html"):
        path.write_text(_pydeprecate_fix_accessibility(path.read_text(encoding="utf-8")), encoding="utf-8")
