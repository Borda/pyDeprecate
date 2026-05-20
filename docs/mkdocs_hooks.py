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


def on_post_page(output: str, page: Page, config: MkDocsConfig, **_kwargs: object) -> str:
    """Inject the llms.txt link directive into every rendered page's <head>."""
    site_url = str(getattr(config, "site_url", "") or "").rstrip("/")
    llms_url = f"{site_url}/llms.txt" if site_url else "/llms.txt"
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
