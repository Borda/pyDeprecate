"""Characterization tests: ``deprecated_instance`` proxies survive pickling, including payloads written by 0.14.0.

Deprecated configuration objects end up in caches, multiprocessing queues and on-disk snapshots, so a proxy pickled by
one release has to load in the next.  The payload references ``deprecate.proxy._reconstruct_proxy`` together with the
``deprecate._types._ProxyConfig`` and ``deprecate._types.DeprecationConfig`` dataclasses, so those import paths and
their state layout are part of the compatibility contract.  These tests pin a fresh round trip at protocol 2 and at
the highest protocol, and check that checked-in payloads produced by the released 0.14.0 wheel still load, warn exactly
once and round-trip again.

"""

import base64
import pickle
import warnings
from typing import Any

import pytest

from deprecate import DeprecatedError, get_deprecation_config
from deprecate._types import DeprecationConfig, _ProxyConfig
from deprecate.proxy import _DeprecatedProxy, _reconstruct_proxy
from tests._helpers import captured_warnings
from tests.collections.api_deprecated import DepPositionalOnlyRequired
from tests.collections.api_deprecated_regressions import make_pickle_fatal_settings_proxy, make_pickle_settings_proxy

#: ``_ProxyConfig`` fields a 0.14.0 payload carries next to its flat ``warned`` / ``warned_args`` counters; read off
#: the checked-in payloads above (0.14 had no ``state`` and no ``facts`` field).
_PROXY_CONFIG_FIELDS_0_14 = (
    "obj",
    "stream",
    "num_warns",
    "read_only",
    "args_extra",
    "message_template",
    "attrs_mapping",
    "skip_if",
)

# Payloads generated once with the released pyDeprecate 0.14.0 wheel in a separate virtualenv (CPython 3.13.16,
# ``pickle.HIGHEST_PROTOCOL == 5``), run in isolated mode (``python -I``) so the repository sources were not importable.
# The script built ``deprecated_instance({"threshold": 0.5, "enabled": True}, name="legacy_settings",
# deprecated_in="1.0", remove_in="2.0")`` — the same arguments as ``make_pickle_settings_proxy`` — and, without touching
# the proxy first, printed ``base64.b64encode(pickle.dumps(proxy, protocol=P))`` for ``P`` in ``(2, 5)``.  The wrapped
# object is a plain ``dict``, so loading needs only ``deprecate``, ``functools.partial``, ``_warnings.warn`` and
# builtins, no test-collection module.  Do not regenerate these with a newer release: their value is that they are old.
_PAYLOAD_0_14_PROTOCOL_2 = (
    "gAJjZGVwcmVjYXRlLnByb3h5Cl9yZWNvbnN0cnVjdF9wcm94eQpxAChjZGVwcmVjYXRlLl90eXBlcwpfUHJveHlDb25maWcK"
    "cQEpgXECfXEDKFgDAAAAb2JqcQR9cQUoWAkAAAB0aHJlc2hvbGRxBkc/4AAAAAAAAFgHAAAAZW5hYmxlZHEHiHVYBgAAAHN0"
    "cmVhbXEIY2Z1bmN0b29scwpwYXJ0aWFsCnEJY193YXJuaW5ncwp3YXJuCnEKhXELUnEMKGgKKX1xDVgIAAAAY2F0ZWdvcnlx"
    "DmNleGNlcHRpb25zCkZ1dHVyZVdhcm5pbmcKcQ9zTnRxEGJYCQAAAG51bV93YXJuc3ERSwFYCQAAAHJlYWRfb25seXESiVgK"
    "AAAAYXJnc19leHRyYXETTlgQAAAAbWVzc2FnZV90ZW1wbGF0ZXEUTlgNAAAAYXR0cnNfbWFwcGluZ3EVTlgHAAAAc2tpcF9p"
    "ZnEWiVgGAAAAd2FybmVkcRdLAFgLAAAAd2FybmVkX2FyZ3NxGH1xGXViY2RlcHJlY2F0ZS5fdHlwZXMKRGVwcmVjYXRpb25D"
    "b25maWcKcRopgXEbfXEcKFgNAAAAZGVwcmVjYXRlZF9pbnEdWAMAAAAxLjBxHlgJAAAAcmVtb3ZlX2lucR9YAwAAADIuMHEg"
    "WAQAAABuYW1lcSFYDwAAAGxlZ2FjeV9zZXR0aW5nc3EiWAYAAAB0YXJnZXRxI05YDAAAAGFyZ3NfbWFwcGluZ3EkTmgTTlgN"
    "AAAAbWlzY29uZmlndXJlZHEliVgPAAAAZG9jc3RyaW5nX3N0eWxlcSZYAwAAAHJzdHEnaBROaBVOWAwAAABhc19leGNlcHRp"
    "b25xKE5YGgAAAGFyZ3NfbWFwcGluZ19hdXRvX2V4cGFuZGVkcSkpWBwAAABhcmdzX21hcHBpbmdfcG9zaXRpb25hbF9vbmx5"
    "cSopWBYAAAB0YXJnZXRfcG9zaXRpb25hbF9vbmx5cStjX19idWlsdGluX18KZnJvemVuc2V0CnEsXXEthXEuUnEvWBwAAAB0"
    "YXJnZXRfcG9zaXRpb25hbF9vbmx5X29yZGVycTApWBYAAABzb3VyY2VfcG9zaXRpb25hbF9vbmx5cTFoLF1xMoVxM1JxNFgc"
    "AAAAc291cmNlX3Bvc2l0aW9uYWxfb25seV9vcmRlcnE1KVgcAAAAc291cmNlX3Zhcl9wb3NpdGlvbmFsX3ByZWZpeHE2SwBY"
    "FgAAAHRhcmdldF9hbGxfcGFyYW1fbmFtZXNxN05YHQAAAHRhcmdldF9hY2NlcHRzX3Zhcl9wb3NpdGlvbmFscTiJWBoAAAB0"
    "YXJnZXRfYWNjZXB0c192YXJfa2V5d29yZHE5iXViWHIBAABkaWN0KCkgLT4gbmV3IGVtcHR5IGRpY3Rpb25hcnkKZGljdCht"
    "YXBwaW5nKSAtPiBuZXcgZGljdGlvbmFyeSBpbml0aWFsaXplZCBmcm9tIGEgbWFwcGluZyBvYmplY3QncwogICAgKGtleSwg"
    "dmFsdWUpIHBhaXJzCmRpY3QoaXRlcmFibGUpIC0+IG5ldyBkaWN0aW9uYXJ5IGluaXRpYWxpemVkIGFzIGlmIHZpYToKICAg"
    "IGQgPSB7fQogICAgZm9yIGssIHYgaW4gaXRlcmFibGU6CiAgICAgICAgZFtrXSA9IHYKZGljdCgqKmt3YXJncykgLT4gbmV3"
    "IGRpY3Rpb25hcnkgaW5pdGlhbGl6ZWQgd2l0aCB0aGUgbmFtZT12YWx1ZSBwYWlycwogICAgaW4gdGhlIGtleXdvcmQgYXJn"
    "dW1lbnQgbGlzdC4gIEZvciBleGFtcGxlOiAgZGljdChvbmU9MSwgdHdvPTIpcTpYTAAAAFRoZSBgbGVnYWN5X3NldHRpbmdz"
    "YCB3YXMgZGVwcmVjYXRlZCBzaW5jZSB2MS4wLiBJdCB3aWxsIGJlIHJlbW92ZWQgaW4gdjIuMC5xO4VxPHRxPVJxPi4="
)
_PAYLOAD_0_14_PROTOCOL_5 = (
    "gAWVHAUAAAAAAACMD2RlcHJlY2F0ZS5wcm94eZSMEl9yZWNvbnN0cnVjdF9wcm94eZSTlCiMEGRlcHJlY2F0ZS5fdHlwZXOU"
    "jAxfUHJveHlDb25maWeUk5QpgZR9lCiMA29iapR9lCiMCXRocmVzaG9sZJRHP+AAAAAAAACMB2VuYWJsZWSUiHWMBnN0cmVh"
    "bZSMCWZ1bmN0b29sc5SMB3BhcnRpYWyUk5SMCV93YXJuaW5nc5SMBHdhcm6Uk5SFlFKUKGgSKX2UjAhjYXRlZ29yeZSMCGJ1"
    "aWx0aW5zlIwNRnV0dXJlV2FybmluZ5STlHNOdJRijAludW1fd2FybnOUSwGMCXJlYWRfb25seZSJjAphcmdzX2V4dHJhlE6M"
    "EG1lc3NhZ2VfdGVtcGxhdGWUTowNYXR0cnNfbWFwcGluZ5ROjAdza2lwX2lmlImMBndhcm5lZJRLAIwLd2FybmVkX2FyZ3OU"
    "fZR1YmgDjBFEZXByZWNhdGlvbkNvbmZpZ5STlCmBlH2UKIwNZGVwcmVjYXRlZF9pbpSMAzEuMJSMCXJlbW92ZV9pbpSMAzIu"
    "MJSMBG5hbWWUjA9sZWdhY3lfc2V0dGluZ3OUjAZ0YXJnZXSUTowMYXJnc19tYXBwaW5nlE5oHU6MDW1pc2NvbmZpZ3VyZWSU"
    "iYwPZG9jc3RyaW5nX3N0eWxllIwDcnN0lGgeTmgfTowMYXNfZXhjZXB0aW9ulE6MGmFyZ3NfbWFwcGluZ19hdXRvX2V4cGFu"
    "ZGVklCmMHGFyZ3NfbWFwcGluZ19wb3NpdGlvbmFsX29ubHmUKYwWdGFyZ2V0X3Bvc2l0aW9uYWxfb25seZQokZSMHHRhcmdl"
    "dF9wb3NpdGlvbmFsX29ubHlfb3JkZXKUKYwWc291cmNlX3Bvc2l0aW9uYWxfb25seZQokZSMHHNvdXJjZV9wb3NpdGlvbmFs"
    "X29ubHlfb3JkZXKUKYwcc291cmNlX3Zhcl9wb3NpdGlvbmFsX3ByZWZpeJRLAIwWdGFyZ2V0X2FsbF9wYXJhbV9uYW1lc5RO"
    "jB10YXJnZXRfYWNjZXB0c192YXJfcG9zaXRpb25hbJSJjBp0YXJnZXRfYWNjZXB0c192YXJfa2V5d29yZJSJdWJYcgEAAGRp"
    "Y3QoKSAtPiBuZXcgZW1wdHkgZGljdGlvbmFyeQpkaWN0KG1hcHBpbmcpIC0+IG5ldyBkaWN0aW9uYXJ5IGluaXRpYWxpemVk"
    "IGZyb20gYSBtYXBwaW5nIG9iamVjdCdzCiAgICAoa2V5LCB2YWx1ZSkgcGFpcnMKZGljdChpdGVyYWJsZSkgLT4gbmV3IGRp"
    "Y3Rpb25hcnkgaW5pdGlhbGl6ZWQgYXMgaWYgdmlhOgogICAgZCA9IHt9CiAgICBmb3IgaywgdiBpbiBpdGVyYWJsZToKICAg"
    "ICAgICBkW2tdID0gdgpkaWN0KCoqa3dhcmdzKSAtPiBuZXcgZGljdGlvbmFyeSBpbml0aWFsaXplZCB3aXRoIHRoZSBuYW1l"
    "PXZhbHVlIHBhaXJzCiAgICBpbiB0aGUga2V5d29yZCBhcmd1bWVudCBsaXN0LiAgRm9yIGV4YW1wbGU6ICBkaWN0KG9uZT0x"
    "LCB0d289MimUjExUaGUgYGxlZ2FjeV9zZXR0aW5nc2Agd2FzIGRlcHJlY2F0ZWQgc2luY2UgdjEuMC4gSXQgd2lsbCBiZSBy"
    "ZW1vdmVkIGluIHYyLjAulIWUdJRSlC4="
)

_PAYLOADS_0_14 = [
    pytest.param(_PAYLOAD_0_14_PROTOCOL_2, id="0.14-protocol-2"),
    pytest.param(_PAYLOAD_0_14_PROTOCOL_5, id="0.14-protocol-5"),
]
_PROTOCOLS = [pytest.param(2, id="protocol-2"), pytest.param(pickle.HIGHEST_PROTOCOL, id="protocol-highest")]
_SETTINGS = {"threshold": 0.5, "enabled": True}
_WARNING_TEXT = "The `legacy_settings` was deprecated since v1.0. It will be removed in v2.0."


def _load(payload: str) -> Any:  # noqa: ANN401
    """Unpickle a base64-encoded payload checked into this module."""
    return pickle.loads(base64.b64decode(payload))  # noqa: S301 - trusted payload generated by this project


def _identity(proxy: Any) -> tuple[bool, bool, Any, str]:  # noqa: ANN401
    """Return the proxy facts a round trip has to preserve, read without emitting a deprecation warning.

    Whether it is still a ``_DeprecatedProxy``, equality with the wrapped settings (a silent probe), the frozen
    ``DeprecationConfig`` and the rendered PEP 702 ``__deprecated__`` marker.

    """
    is_proxy = issubclass(type(proxy), _DeprecatedProxy)
    return is_proxy, proxy == _SETTINGS, proxy.__deprecation_config__, proxy.__deprecated__


def _future_warning_texts(caught: list[warnings.WarningMessage]) -> list[str]:
    """Return the messages of the caught ``FutureWarning`` records, in emission order."""
    return [str(w.message) for w in caught if issubclass(w.category, FutureWarning)]


class TestFreshRoundTrip:
    """A proxy built by the current release pickles and unpickles at protocol 2 and at the highest protocol."""

    @pytest.mark.parametrize("protocol", _PROTOCOLS)
    def test_preserves_proxy(self, protocol: int) -> None:
        """The unpickled object is still a deprecation proxy over equal data with identical metadata.

        A deprecated settings object is sent to a worker process: the copy must still be a ``_DeprecatedProxy`` (so it
        keeps warning), compare equal to the original data, and carry the same ``DeprecationConfig`` and PEP 702 marker.

        """
        proxy = make_pickle_settings_proxy()

        restored = pickle.loads(pickle.dumps(proxy, protocol=protocol))  # noqa: S301 - payload produced in this test

        assert _identity(restored) == _identity(proxy)

    @pytest.mark.parametrize("protocol", _PROTOCOLS)
    def test_payload_names_no_post_0_14_class(self, protocol: int) -> None:
        """The payload references no private class that 0.14 lacks, so an older release can still load it.

        A cache or queue shared by processes on different pyDeprecate releases holds proxies pickled by either one.
        0.14 has neither ``_WrapperState`` on the proxy config nor ``_CallFacts``; a payload naming either would fail
        to load there with ``AttributeError: Can't get attribute``.
        """
        payload = pickle.dumps(make_pickle_settings_proxy(), protocol=protocol)

        assert (b"_CallFacts" in payload, b"_WrapperState" in payload) == (False, False)

    @pytest.mark.parametrize("protocol", _PROTOCOLS)
    def test_restored_proxy_warns_once(self, protocol: int) -> None:
        """An unpickled proxy with the default budget warns on its first use and stays silent afterwards.

        The budget counter travels in the payload; a proxy pickled before any access arrives with its single warning
        still available, so two reads on the copy produce exactly one ``FutureWarning``.

        """
        restored = pickle.loads(pickle.dumps(make_pickle_settings_proxy(), protocol=protocol))  # noqa: S301 - own round trip
        with captured_warnings() as caught:
            values = (restored["threshold"], restored["enabled"])

        assert (values, _future_warning_texts(caught)) == ((0.5, True), [_WARNING_TEXT])

    @pytest.mark.parametrize("protocol", _PROTOCOLS)
    def test_spent_budget_stays_silent(self, protocol: int) -> None:
        """An unpickled proxy whose single warning was already spent never warns again.

        A deprecated settings object is read once in the parent process (the one allowed warning) and then sent to a
        worker: the ``warned`` counter travels in the payload, so the copy must not spend a second budget and repeat the
        warning on every worker.

        """
        proxy = make_pickle_settings_proxy()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            _ = proxy["threshold"]
        restored = pickle.loads(pickle.dumps(proxy, protocol=protocol))  # noqa: S301 - payload produced in this test
        with captured_warnings() as caught:
            values = (restored["threshold"], restored["enabled"])

        assert (values, _future_warning_texts(caught)) == ((0.5, True), [])

    @pytest.mark.parametrize("protocol", _PROTOCOLS)
    def test_fatal_proxy_still_raises(self, protocol: int) -> None:
        """An unpickled proxy built with ``as_exception=True`` still raises ``DeprecatedError`` on first use.

        A fatal deprecation is a closed door: a copy sent to another process must not quietly turn back into a warn-once
        proxy, so the non-default ``as_exception`` setting has to survive the round trip.

        """
        restored = pickle.loads(  # noqa: S301 - payload produced in this test
            pickle.dumps(make_pickle_fatal_settings_proxy(), protocol=protocol)
        )

        with pytest.raises(DeprecatedError, match="legacy_settings"):
            _ = restored["threshold"]


class TestPayloadFrom014:
    """Payloads pickled by pyDeprecate 0.14.0 load with the current release."""

    @pytest.mark.parametrize("payload", _PAYLOADS_0_14)
    def test_loads_as_proxy(self, payload: str) -> None:
        """A 0.14.0 payload loads into a proxy identical to one built today with the same arguments.

        An application upgraded pyDeprecate while a cache still holds proxies pickled by the old release.  Loading
        must resolve the reconstructor and both config dataclasses at their 0.14.0 import paths and yield the same
        data, ``DeprecationConfig`` and ``__deprecated__`` marker as a freshly built proxy.

        """
        loaded = _load(payload)

        assert _identity(loaded) == _identity(make_pickle_settings_proxy())

    @pytest.mark.parametrize("payload", _PAYLOADS_0_14)
    def test_warns_once(self, payload: str) -> None:
        """A loaded 0.14.0 payload warns on its first use and stays silent afterwards.

        The old payload carries the unspent ``num_warns=1`` budget and the default ``FutureWarning`` stream; two reads
        through the loaded proxy produce exactly one warning with the 0.14.0 message text.

        """
        loaded = _load(payload)
        with captured_warnings() as caught:
            values = (loaded["threshold"], loaded["enabled"])

        assert (values, _future_warning_texts(caught)) == ((0.5, True), [_WARNING_TEXT])

    @pytest.mark.parametrize("payload", _PAYLOADS_0_14)
    def test_round_trips_again(self, payload: str) -> None:
        """A loaded 0.14.0 payload can be pickled again by the current release and still loads unchanged.

        Long-lived caches re-serialise what they read; the proxy rebuilt from the old payload must pickle at the
        highest protocol and come back with the same identity facts.

        """
        loaded = _load(payload)

        again = pickle.loads(pickle.dumps(loaded, protocol=pickle.HIGHEST_PROTOCOL))  # noqa: S301 - own round trip

        assert _identity(again) == _identity(loaded)


class TestPositionalOnlyFactsFrom014:
    """A 0.14-layout config, which carries no call-time ``facts``, keeps the positional-only constructor split."""

    def test_rebuilds_positional_only_split(self) -> None:
        """``_reconstruct_proxy`` rebuilds the split facts from the metadata, so a remapped keyword still lands.

        A ``deprecated_class`` proxy whose ``args_mapping`` renames ``old_val`` to the positional-only ``new_val``
        forwards that value positionally.  0.14 kept the split data only on ``DeprecationConfig``; the config read
        from such a payload has no ``facts`` and, without the rebuild, the constructor would get ``new_val=`` as a
        keyword and raise.  The config is restored through ``_ProxyConfig.__setstate__`` and ``_reconstruct_proxy``,
        the two calls ``pickle.loads`` makes for a proxy payload (a class proxy pickles its class by reference, so no
        checked-in 0.14 payload can carry one).
        """
        live_cfg = object.__getattribute__(DepPositionalOnlyRequired, "_DeprecatedProxy__config")
        dep = get_deprecation_config(DepPositionalOnlyRequired)
        assert isinstance(dep, DeprecationConfig)
        cfg = _ProxyConfig.__new__(_ProxyConfig)
        cfg.__setstate__(
            {name: getattr(live_cfg, name) for name in _PROXY_CONFIG_FIELDS_0_14} | {"warned": 0, "warned_args": {}}
        )
        restored = _reconstruct_proxy(cfg, dep, None)
        with pytest.warns(FutureWarning):
            built = restored(old_val=7)
        assert built.new_val == 7
        assert cfg.facts.target_positional_only == dep.target_positional_only == frozenset({"new_val"})
