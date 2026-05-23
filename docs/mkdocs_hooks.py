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


def on_page_context(
    context: dict[str, object], page: Page, config: MkDocsConfig, nav: object
) -> dict[str, object]:
    """Inject canonical URLs into the page context."""
    canonical = _pydeprecate_public_url(page)
    context["pydeprecate_canonical_url"] = canonical
    page.canonical_url = canonical
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
