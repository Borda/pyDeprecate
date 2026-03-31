"""MkDocs hooks for AI-agent discoverability.

Injects a ``<link>`` element pointing to ``/llms.txt`` into every page's
``<head>`` so that AI agents can discover the structured documentation index.
"""


def on_post_page(output: str, **_kwargs: object) -> str:
    """Inject the llms.txt link directive into every rendered page's <head>."""
    link_tag = '  <link rel="alternate" type="text/markdown" href="/llms.txt">\n'
    return output.replace("</head>", f"{link_tag}</head>", 1)
