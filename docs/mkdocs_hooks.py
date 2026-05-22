"""MkDocs hooks for AI-agent discoverability.

Injects a ``<link>`` element pointing to ``/llms.txt`` into every page's ``<head>`` so that AI agents can discover the
structured documentation index.

"""

from __future__ import annotations

import logging
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
_pydeprecate_config: object = None


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


def _pydeprecate_existing_on_post_page(output: str, page: Page, config: MkDocsConfig, **_kwargs: object) -> str:
    """Inject the llms.txt link directive into every rendered page's <head>."""
    site_url = str(getattr(config, "site_url", "") or "").rstrip("/")
    # mike overrides site_url with a versioned path (e.g. .../v0.8.0) during versioned builds;
    # strip the trailing version/alias segment so the link always points to the root llms.txt.
    root_url = re.sub(r"/(?:v?\d[\w.]*|stable|dev|latest)$", "", site_url)
    llms_url = f"{root_url}/llms.txt" if root_url else "/llms.txt"
    link_tag = f'  <link rel="alternate" type="text/markdown" href="{llms_url}">\n'
    head_marker = "</head>"
    html_marker = "</html>"

    if head_marker in output:
        # Normal case: inject the link tag before the closing </head>.
        return output.replace(head_marker, f"{link_tag}{head_marker}", 1)

    logger = logging.getLogger(__name__)

    if html_marker in output:
        # Fallback: no </head>, inject before </html> so the link is still discoverable.
        logger.warning("mkdocs_hooks.on_post_page: '</head>' not found; injecting llms.txt link before '</html>'.")
        return output.replace(html_marker, f"{link_tag}{html_marker}", 1)

    # Last-resort fallback: append the link at the end of the document.
    logger.warning(
        "mkdocs_hooks.on_post_page: neither '</head>' nor '</html>' found; appending llms.txt link at end of document."
    )
    return f"{output}\n{link_tag}"


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
        root = str((_pydeprecate_config.extra or {}).get("root_site_url", "") or "").rstrip("/")
        if root:
            return f"{root}/stable/"
    return _PYDEPRECATE_STABLE_BASE


def _pydeprecate_root_url() -> str:
    """Return the root site URL (no version suffix), derived from config when available."""
    if _pydeprecate_config is not None:
        root = str((_pydeprecate_config.extra or {}).get("root_site_url", "") or "").rstrip("/")
        if root:
            return root
    return _PYDEPRECATE_PUBLIC_ROOT


_PYDEPRECATE_MARKDOWN_MIRRORS = (
    "index.md",
    "getting-started.md",
    "guide/use-cases.md",
    "guide/audit.md",
    "guide/cli.md",
    "guide/customization.md",
    "guide/migration.md",
    "troubleshooting.md",
    "guide/python-deprecation-decorator.md",
    "guide/replace-warnings-warn.md",
    "guide/deprecate-arguments.md",
    "guide/api-migration-ci.md",
    "guide/agent-recipes.md",
)


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


def _pydeprecate_page_title(page: Page) -> str:
    title = getattr(page, "title", None)
    return title or "pyDeprecate documentation"


def _pydeprecate_page_description(page: Page) -> str:
    meta = getattr(page, "meta", {}) or {}
    return meta.get(
        "description",
        "Python deprecation decorator library for API migration, forwarding, argument renaming, and CI audit checks.",
    )


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
    if replaced == output:
        # Tag was absent — insert it so the mirror URL is always discoverable.
        output = output.replace("</head>", f"{markdown_tag}\n</head>", 1)
    else:
        output = replaced
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
    for src_name in _PYDEPRECATE_MARKDOWN_MIRRORS:
        src = docs / src_name
        if not src.exists():
            continue
        dest = site / _pydeprecate_mirror_target(src_name)
        dest.parent.mkdir(parents=True, exist_ok=True)
        _pydeprecate_shutil.copyfile(src, dest)


def _pydeprecate_rewrite_sitemap(site_dir: str) -> None:
    sitemap = _PyDeprecatePath(site_dir) / "sitemap.xml"
    if not sitemap.exists():
        return
    text = sitemap.read_text(encoding="utf-8")
    text = re.sub(
        r"https://borda\.github\.io/pyDeprecate/(?:v?[^/]+|latest|dev)/",
        _pydeprecate_stable_base(),
        text,
    )
    sitemap.write_text(text, encoding="utf-8")


def on_post_build(config: MkDocsConfig) -> None:
    """Copy markdown mirrors and rewrite sitemap after build completes."""
    _pydeprecate_copy_markdown_mirrors(config["docs_dir"], config["site_dir"])
    _pydeprecate_rewrite_sitemap(config["site_dir"])
