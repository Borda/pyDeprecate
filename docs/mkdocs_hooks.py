"""MkDocs hooks for AI-agent discoverability.

Injects a ``<link>`` element pointing to ``/llms.txt`` into every page's
``<head>`` so that AI agents can discover the structured documentation index.
"""

from __future__ import annotations

import logging
import os
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mkdocs.config.defaults import MkDocsConfig
    from mkdocs.structure.pages import Page


def on_config(config: MkDocsConfig, **_kwargs: object) -> MkDocsConfig:
    """Inject package version into extra config so templates can reference it."""
    try:
        about = os.path.join(os.path.dirname(os.path.dirname(__file__)), "src", "deprecate", "__about__.py")
        with open(about) as f:
            match = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', f.read())
        config.extra["package_version"] = match.group(1) if match else ""
    except Exception:
        config.extra["package_version"] = ""
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
import json as _pydeprecate_json
import re as _pydeprecate_re
import shutil as _pydeprecate_shutil
from pathlib import Path as _PyDeprecatePath
from xml.etree import ElementTree as _PyDeprecateElementTree

_PYDEPRECATE_PUBLIC_ROOT = "https://borda.github.io/pyDeprecate"
_PYDEPRECATE_STABLE_BASE = f"{_PYDEPRECATE_PUBLIC_ROOT}/stable/"
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


def _pydeprecate_public_url(page):
    page_url = getattr(page, "url", "") or ""
    if page_url in {"", "index.html"}:
        return _PYDEPRECATE_STABLE_BASE
    return f"{_PYDEPRECATE_STABLE_BASE}{page_url}"


def _pydeprecate_markdown_url(page):
    page_url = getattr(page, "url", "") or "index.html"
    if page_url == "":
        page_url = "index.html"
    return f"{_PYDEPRECATE_STABLE_BASE}{page_url}.md"


def _pydeprecate_page_title(page):
    title = getattr(page, "title", None)
    return title or "pyDeprecate documentation"


def _pydeprecate_page_description(page):
    meta = getattr(page, "meta", {}) or {}
    return meta.get(
        "description",
        "Python deprecation decorator library for API migration, forwarding, argument renaming, and CI audit checks.",
    )


def _pydeprecate_json_ld(page):
    src_path = getattr(getattr(page, "file", None), "src_path", "")
    url = _pydeprecate_public_url(page)
    title = _pydeprecate_page_title(page)
    description = _pydeprecate_page_description(page)
    base = {
        "@context": "https://schema.org",
        "@type": "TechArticle",
        "headline": title,
        "description": description,
        "url": url,
        "mainEntityOfPage": {"@type": "WebPage", "@id": url},
        "author": {"@type": "Person", "name": "Jan Kybic"},
        "about": "Python API deprecation and migration",
        "programmingLanguage": "Python",
    }
    objects = [base]
    if src_path == "getting-started.md":
        objects.append({
            "@context": "https://schema.org",
            "@type": "HowTo",
            "name": "Add a Python deprecation decorator with pyDeprecate",
            "description": description,
            "url": url,
            "step": [
                {"@type": "HowToStep", "name": "Install pyDeprecate"},
                {"@type": "HowToStep", "name": "Import from deprecate"},
                {"@type": "HowToStep", "name": "Wrap the old API with deprecated"},
            ],
        })
    if src_path == "guide/use-cases.md":
        objects.append({
            "@context": "https://schema.org",
            "@type": "ItemList",
            "name": "pyDeprecate migration use cases",
            "url": url,
            "itemListElement": [
                {"@type": "ListItem", "position": 1, "name": "Function rename"},
                {"@type": "ListItem", "position": 2, "name": "Argument rename"},
                {"@type": "ListItem", "position": 3, "name": "Class rename"},
                {"@type": "ListItem", "position": 4, "name": "CI audit"},
            ],
        })
    if src_path in {"guide/audit.md", "guide/cli.md", "guide/api-migration-ci.md"}:
        objects.append({
            "@context": "https://schema.org",
            "@type": "SoftwareSourceCode",
            "name": title,
            "description": description,
            "url": url,
            "programmingLanguage": "Python",
            "runtimePlatform": "Python",
        })
    if src_path == "changelog.md":
        objects.append({
            "@context": "https://schema.org",
            "@type": "Article",
            "headline": title,
            "description": description,
            "url": url,
            "mainEntityOfPage": {"@type": "WebPage", "@id": url},
        })
    return objects


def on_page_context(context, page, config, nav):
    context["pydeprecate_canonical_url"] = _pydeprecate_public_url(page)
    page.canonical_url = context["pydeprecate_canonical_url"]
    return context


def on_post_page(output, page, config):
    if "_pydeprecate_existing_on_post_page" in globals():
        output = _pydeprecate_existing_on_post_page(output, page, config)
    canonical = _pydeprecate_public_url(page)
    markdown = _pydeprecate_markdown_url(page)
    output = _pydeprecate_re.sub(
        r'<link rel="canonical" href="[^"]+">',
        f'<link rel="canonical" href="{canonical}">',
        output,
    )
    output = _pydeprecate_re.sub(
        r'<meta property="og:url" content="[^"]+">',
        f'<meta property="og:url" content="{canonical}">',
        output,
    )
    output = _pydeprecate_re.sub(
        r'<link rel="alternate" type="text/markdown" href="[^"]+">',
        f'<link rel="alternate" type="text/markdown" href="{markdown}">',
        output,
    )
    if 'href="https://borda.github.io/pyDeprecate/llms.txt"' not in output:
        output = output.replace(
            "</head>",
            '<link rel="alternate" type="text/plain" title="llms.txt" href="https://borda.github.io/pyDeprecate/llms.txt">\n</head>',
        )
    graph = _pydeprecate_json_ld(page)
    script = '<script type="application/ld+json">' + _pydeprecate_json.dumps(graph, ensure_ascii=False) + '</script>'
    output = output.replace("</head>", f"{script}\n</head>")
    return output


def _pydeprecate_mirror_target(src_path):
    if src_path == "index.md":
        return "index.html.md"
    return src_path.removesuffix(".md") + ".html.md"


def _pydeprecate_copy_markdown_mirrors(docs_dir, site_dir):
    docs = _PyDeprecatePath(docs_dir)
    site = _PyDeprecatePath(site_dir)
    for src_name in _PYDEPRECATE_MARKDOWN_MIRRORS:
        src = docs / src_name
        if not src.exists():
            continue
        dest = site / _pydeprecate_mirror_target(src_name)
        dest.parent.mkdir(parents=True, exist_ok=True)
        _pydeprecate_shutil.copyfile(src, dest)


def _pydeprecate_rewrite_sitemap(site_dir):
    sitemap = _PyDeprecatePath(site_dir) / "sitemap.xml"
    if not sitemap.exists():
        return
    text = sitemap.read_text(encoding="utf-8")
    text = _pydeprecate_re.sub(
        r"https://borda\.github\.io/pyDeprecate/(?:v[^/]+|latest|dev)/",
        _PYDEPRECATE_STABLE_BASE,
        text,
    )
    sitemap.write_text(text, encoding="utf-8")


def on_post_build(config):
    _pydeprecate_copy_markdown_mirrors(config["docs_dir"], config["site_dir"])
    _pydeprecate_rewrite_sitemap(config["site_dir"])
