"""MkDocs hooks for AI-agent discoverability.

Injects a ``<link>`` element pointing to ``/llms.txt`` into every page's
``<head>`` so that AI agents can discover the structured documentation index.
"""

import logging


def on_post_page(output: str, **_kwargs: object) -> str:
    """Inject the llms.txt link directive into every rendered page's <head>."""
    link_tag = '  <link rel="alternate" type="text/markdown" href="/llms.txt">\n'
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
