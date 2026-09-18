"""Unit tests for private helpers in :mod:`deprecate.audit._report`."""

import warnings

import tests.collection_misconfigured as clean_module
from deprecate.audit._report import _format_report_target
from deprecate.proxy import deprecated_class


class TestFormatReportProxyTarget:
    """_format_report_target reads a chained-proxy target statically, never via dynamic ``getattr``."""

    def test_chained_proxy_target_formatted_statically(self) -> None:
        """A target that is itself a deprecated_class proxy is formatted by its declared name, silently.

        When a deprecated alias forwards to *another* deprecated alias (the chain
        ``validate_deprecation_chains`` exists to flag), rendering its report row must show the immediate
        target's real declared name — not a fabricated path spliced from the proxy class's ``__module__``
        and the innermost target's ``__qualname__`` — and must not burn the chained proxy's warn budget
        from inside the audit tooling.
        """
        final_cls = type("FinalApi", (), {})
        mid = deprecated_class(target=final_cls, deprecated_in="1.0", remove_in="2.0")(type("MidApi", (), {}))
        old = deprecated_class(target=mid, deprecated_in="1.0", remove_in="2.0")(type("OldApi", (), {}))
        target = object.__getattribute__(old, "__deprecation_config__").target

        with warnings.catch_warnings():
            warnings.simplefilter("error")  # any warning emitted during formatting fails the test
            formatted = _format_report_target(target)

        mid_cfg = object.__getattribute__(mid, "_DeprecatedProxy__config")
        assert formatted == "MidApi"
        assert mid_cfg.warned == 0

    def test_legacy_proxy_target_formatted_through_public_fallback(self) -> None:
        """Format a proxy target that carries only the pre-v0.13 metadata attribute.

        Mixed-version reports can contain a chained proxy created by an older pyDeprecate installation. Rendering
        must preserve its declared alias name without directly reading the absent new attribute or consuming a warning.
        """
        target = clean_module.make_legacy_metadata_proxy()

        with warnings.catch_warnings():
            warnings.simplefilter("error")
            formatted = _format_report_target(target)

        proxy_config = object.__getattribute__(target, "_DeprecatedProxy__config")
        assert formatted == "LegacyClass"
        assert proxy_config.warned == 0
