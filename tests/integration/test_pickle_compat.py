"""Characterization tests: deprecated proxies and the instances they build survive pickling, across releases.

Deprecated configuration objects end up in caches, multiprocessing queues and on-disk snapshots, so a proxy pickled by
one release has to load in the next.  The payload references ``deprecate.proxy._reconstruct_proxy`` together with the
``deprecate._types._ProxyConfig`` and ``deprecate._types.DeprecationConfig`` dataclasses, so those import paths and
their state layout are part of the compatibility contract.  These tests pin a fresh round trip at protocol 2 and at
the highest protocol, and check that checked-in payloads produced by the released 0.14.0 wheel still load, warn exactly
once and round-trip again.

Instances of a class deprecated in place (``@deprecated_class`` without a replacement class) are ordinary objects of
the wrapped class, but the module-level name now holds the proxy, so pickle cannot save the class by reference.  Their
payload instead references ``deprecate.proxy._rebuild_class_instance`` (``_rebuild_class_instance_ex`` for keyword
``__new__`` arguments) with the class's module and qualified name, which makes those two import paths part of the
contract too.

"""

import base64
import copy
import copyreg
import pickle
import subprocess
import sys
import warnings
from typing import Any, Callable

import pytest

from deprecate import DeprecatedError, get_deprecation_config
from deprecate._types import DeprecationConfig, _ProxyConfig
from deprecate.proxy import _DeprecatedProxy, _reconstruct_proxy
from tests._helpers import captured_warnings
from tests.collections import api_deprecated_regressions
from tests.collections.api_current import InPlaceAliasSource
from tests.collections.api_deprecated import DepPositionalOnlyRequired, DeprecatedEnum, RedirectedDataClass
from tests.collections.api_deprecated_regressions import (
    DeprecatedInPlaceHost,
    InPlaceCustomState,
    InPlaceDict,
    InPlaceError,
    InPlaceHost,
    InPlaceKeywordNew,
    InPlaceList,
    InPlaceOrderedDict,
    InPlaceOwnReduce,
    InPlaceSettings,
    InPlaceSlots,
    make_pickle_fatal_settings_proxy,
    make_pickle_settings_proxy,
)

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


#: Every pickle protocol this interpreter supports, ``0`` through ``pickle.HIGHEST_PROTOCOL``.
_ALL_PROTOCOLS = list(range(pickle.HIGHEST_PROTOCOL + 1))

#: One sample instance per reduce shape, built through the in-place deprecated alias (each call warns).
_IN_PLACE_BUILDERS = [
    pytest.param(lambda: InPlaceSettings(level=2, tags=["beta"]), id="dict-state"),
    pytest.param(lambda: InPlaceSlots(level=2), id="slots"),
    pytest.param(lambda: InPlaceCustomState(2), id="getstate-setstate"),
    pytest.param(lambda: InPlaceKeywordNew(level=2), id="keyword-new"),
    pytest.param(lambda: InPlaceList([1, 2]), id="list-items"),
    pytest.param(lambda: InPlaceDict(a=1), id="dict-items"),
    pytest.param(lambda: InPlaceOrderedDict(a=1), id="inherited-class-call-reduce"),
    pytest.param(lambda: InPlaceHost.Inner(level=2), id="nested-in-plain-host"),
    pytest.param(lambda: DeprecatedInPlaceHost.Inner(level=2), id="nested-in-deprecated-host"),
]


def _build_in_place(build: Callable[[], Any]) -> Any:  # noqa: ANN401
    """Build a sample instance through its deprecated alias, asserting the alias still warns on use."""
    with pytest.warns(FutureWarning):
        return build()


def _deprecation_messages(caught: list[warnings.WarningMessage]) -> list[str]:
    """Return the text of every ``FutureWarning`` caught, the category pyDeprecate's default stream emits."""
    return [str(w.message) for w in caught if issubclass(w.category, FutureWarning)]


def _run_in_fresh_interpreter(code: str) -> str:
    """Run *code* in a new interpreter that imports from the same paths as this one and return its stripped stdout.

    Not ``python -I``: isolated mode ignores the path setup, so a child could import an installed ``deprecate`` that is
    not the one under test. Copying ``sys.path`` makes ``deprecate`` and ``tests.collections`` resolve exactly as here.

    """
    prologue = f"import sys\nsys.path[:0] = {sys.path!r}\n"
    result = subprocess.run(
        [sys.executable, "-c", prologue + code], capture_output=True, text=True, check=True, timeout=60
    )
    return result.stdout.strip()


class TestInPlaceClassInstances:
    """Instances of a class deprecated in place pickle as instances of the class, silently, at every protocol."""

    @pytest.mark.parametrize("protocol", _ALL_PROTOCOLS)
    @pytest.mark.parametrize("build", _IN_PLACE_BUILDERS)
    def test_round_trips(self, build: Callable[[], Any], protocol: int) -> None:
        """The unpickled object is an instance of the wrapped class with equal state, at every protocol.

        A deprecated settings class keeps working during its migration window while its instances are sent to worker
        processes or cached on disk. Before the fix ``pickle.dumps`` raised ``PicklingError`` because the module-level
        name is the proxy; every reduce shape (``__dict__``, ``__slots__``, ``__getstate__``, keyword ``__new__``,
        list and dict items, nested qualnames) must come back as the same class with the same state.

        """
        instance = _build_in_place(build)

        restored = pickle.loads(pickle.dumps(instance, protocol=protocol))  # noqa: S301 - payload produced in this test

        assert (type(restored) is type(instance), restored == instance) == (True, True)

    @pytest.mark.parametrize("protocol", _ALL_PROTOCOLS)
    def test_runs_custom_state_hooks(self, protocol: int) -> None:
        """A class's own ``__getstate__``/``__setstate__`` still decide what travels and how it is restored.

        The class drops its cache from the pickled state and starts an empty one on load; the reducer reuses the
        default reduction, so the cache must arrive empty while the original keeps its entry.

        """
        instance = _build_in_place(lambda: InPlaceCustomState(2))

        restored = pickle.loads(pickle.dumps(instance, protocol=protocol))  # noqa: S301 - payload produced in this test

        assert (restored.cache, instance.cache) == ({}, {2: 2})

    @pytest.mark.parametrize("build", _IN_PLACE_BUILDERS)
    def test_round_trip_is_silent(self, build: Callable[[], Any]) -> None:
        """Pickling and unpickling an instance emits no deprecation warning.

        Serialisation is structural machinery, not a use of the deprecated name: a worker loading a queued instance
        must not warn on every load, so resolving the class from its module (through the proxy that now holds the
        name, and through a deprecated host class) has to read the wrapped class without any attribute access that
        warns. The fixture classes warn on every use, so a single stray access would show up here.

        """
        instance = _build_in_place(build)
        with captured_warnings() as caught:
            pickle.loads(pickle.dumps(instance, protocol=pickle.HIGHEST_PROTOCOL))  # noqa: S301 - own round trip

        assert _deprecation_messages(caught) == []

    @pytest.mark.subprocess
    def test_loads_in_fresh_interpreter(self) -> None:
        """A payload pickled here loads silently in a new interpreter that has not imported the fixture module yet.

        The real consumers of such a payload are other processes (a ``multiprocessing`` worker, a later run reading a
        cache): unpickling has to import the defining module itself and resolve the class through the proxy published
        under its name, with no ``FutureWarning`` naming the deprecated class.

        """
        instance = _build_in_place(lambda: InPlaceSettings(level=3, tags=["beta"]))
        payload = base64.b64encode(pickle.dumps(instance, protocol=pickle.HIGHEST_PROTOCOL)).decode()
        code = (
            "import base64, pickle, warnings\n"
            "with warnings.catch_warnings(record=True) as caught:\n"
            "    warnings.simplefilter('always')\n"
            f"    restored = pickle.loads(base64.b64decode({payload!r}))\n"
            "named = [str(w.message) for w in caught if 'InPlaceSettings' in str(w.message)]\n"
            "print((type(restored).__qualname__, restored.level, restored.tags, named))\n"
        )

        stdout = _run_in_fresh_interpreter(code)

        assert stdout == repr(("InPlaceSettings", 3, ["beta"], []))

    @pytest.mark.parametrize("copier", [pytest.param(copy.copy, id="copy"), pytest.param(copy.deepcopy, id="deepcopy")])
    def test_copy_still_builds_equal_instance(self, copier: Callable[[Any], Any]) -> None:
        """``copy.copy`` and ``copy.deepcopy`` still return a new, equal instance of the wrapped class.

        Both consult ``copyreg.dispatch_table``, which now holds a reducer for the class, so they run through the same
        reduce tuple as pickle. The result must not change: a distinct object of the same class with equal state.

        """
        instance = _build_in_place(lambda: InPlaceSettings(level=2, tags=["beta"]))

        duplicate = copier(instance)

        assert (duplicate is instance, type(duplicate) is type(instance), duplicate == instance) == (False, True, True)

    @pytest.mark.parametrize(
        ("copier", "shares_tags"),
        [pytest.param(copy.copy, True, id="copy-shares"), pytest.param(copy.deepcopy, False, id="deepcopy-copies")],
    )
    def test_copy_keeps_its_depth(self, copier: Callable[[Any], Any], shares_tags: bool) -> None:
        """A shallow copy shares nested mutable state and a deep copy duplicates it, exactly as before the reducer.

        A reducer that rebuilt the state wrongly would make a shallow copy deep or a deep copy shallow; mutating the
        copy's ``tags`` list after a deep copy must not leak into the original.

        """
        instance = _build_in_place(lambda: InPlaceSettings(level=2, tags=["beta"]))

        duplicate = copier(instance)

        assert (duplicate.tags is instance.tags) is shares_tags

    def test_class_object_stays_unpicklable(self) -> None:
        """Pickling the deprecated class alias itself still raises ``PicklingError``.

        Pickle saves a class by reference and checks that the module-level name is that very object; the name holds
        the proxy, so the class cannot be pickled by reference. Only its instances are made picklable.

        """
        with pytest.raises(pickle.PicklingError):
            pickle.dumps(InPlaceSettings)

    def test_alias_form_pickles_by_reference(self) -> None:
        """When the class keeps its own name, its instances pickle by reference to the class, as before.

        ``Alias = deprecated_class(...)(Cls)`` leaves ``Cls`` bound to the class, so the default reduction already
        works; the reducer must hand it through unchanged instead of routing the payload through pyDeprecate.

        """
        payload = pickle.dumps(InPlaceAliasSource(4), protocol=pickle.HIGHEST_PROTOCOL)

        assert (b"_rebuild_class_instance" in payload, pickle.loads(payload) == InPlaceAliasSource(4)) == (False, True)  # noqa: S301

    @pytest.mark.parametrize(
        ("build", "unpublish"),
        [
            pytest.param(
                lambda: InPlaceSettings(level=2),
                lambda mp: mp.setattr(api_deprecated_regressions, "InPlaceSettings", object()),
                id="rebound-to-other-object",
            ),
            pytest.param(
                lambda: InPlaceSettings(level=2),
                lambda mp: mp.delattr(api_deprecated_regressions, "InPlaceSettings"),
                id="name-deleted",
            ),
            pytest.param(
                lambda: InPlaceHost.Inner(level=2),
                lambda mp: mp.setattr(api_deprecated_regressions, "InPlaceHost", 5),
                id="host-without-namespace",
            ),
        ],
    )
    def test_unpublished_class_falls_back_to_pickle_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
        build: Callable[[], Any],
        unpublish: Callable[[pytest.MonkeyPatch], None],
    ) -> None:
        """When the class's qualname no longer leads to a proxy over it, pickling fails as pickle itself would.

        The name may be rebound to another object, deleted, or (for a nested class) its host replaced by an object with
        no namespace. Rebuilding through whatever the name holds now would load an object of the wrong class, so the
        reducer falls back to the default reduction and pickle raises its own ``PicklingError``.

        """
        instance = _build_in_place(build)
        unpublish(monkeypatch)

        with pytest.raises(pickle.PicklingError):
            pickle.dumps(instance)

    def test_load_fails_when_class_is_gone(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Loading a payload whose class was removed from its module raises ``AttributeError`` naming the class.

        A cache written by an older release outlives the class: the rebuild helper must fail loudly with the dotted path
        it looked for, as pickle does for a missing class, instead of building something else.

        """
        payload = pickle.dumps(_build_in_place(lambda: InPlaceSettings(level=2)))
        monkeypatch.setattr(api_deprecated_regressions, "InPlaceSettings", object())

        with pytest.raises(AttributeError, match=r"regressions\.InPlaceSettings` no longer names a class"):
            pickle.loads(payload)  # noqa: S301 - payload produced in this test

    def test_reduce_added_later_is_used(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A ``__reduce__`` the class gains after decoration is honoured instead of the registered reduction.

        The reducer stays registered, but it starts from ``object.__reduce_ex__``, which defers to an overriding
        ``__reduce__``; whatever that method returns must pass through untouched.

        """
        instance = _build_in_place(lambda: InPlaceSettings(level=2))
        monkeypatch.setattr(type(instance), "__reduce__", lambda self: (dict, ({"level": self.level},)))

        restored = pickle.loads(pickle.dumps(instance))  # noqa: S301 - payload produced in this test

        assert restored == {"level": 2}

    def test_reduce_added_later_for_another_class_is_not_rewritten(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A late ``__reduce__`` naming a different class through ``__newobj__`` reaches pickle unchanged.

        Only a reduction naming the deprecated class itself needs its class reference swapped for the rebuild helper.
        Rewriting any other one would silently load an instance of the deprecated class; pickle must instead reject the
        mismatched ``__newobj__`` class, as it does for an undeprecated class.

        """
        instance = _build_in_place(lambda: InPlaceSettings(level=2))
        monkeypatch.setattr(type(instance), "__reduce__", lambda self: (copyreg.__newobj__, (dict,), None))  # type: ignore[attr-defined]

        with pytest.raises(pickle.PicklingError, match="__newobj__"):
            pickle.dumps(instance)

    def test_reduce_ex_added_later_is_used(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A ``__reduce_ex__`` the class gains after decoration decides the payload for pickle and copy.

        ``copyreg.dispatch_table`` takes precedence over an instance's ``__reduce_ex__``, so the registered reducer must
        hand over to it; otherwise the class's own protocol would be silently ignored.

        """
        instance = _build_in_place(lambda: InPlaceSettings(level=2))
        monkeypatch.setattr(type(instance), "__reduce_ex__", lambda self, protocol: (dict, ({"level": self.level},)))

        restored = pickle.loads(pickle.dumps(instance))  # noqa: S301 - payload produced in this test

        assert (restored, copy.copy(instance)) == ({"level": 2}, {"level": 2})

    @pytest.mark.parametrize("protocol", _ALL_PROTOCOLS)
    def test_exception_round_trips(self, protocol: int) -> None:
        """An exception deprecated in place pickles as itself with its args, as process pools need.

        ``concurrent.futures`` and ``multiprocessing`` pickle an exception raised in a worker to re-raise it in the
        parent. ``BaseException.__reduce__`` names the class itself as the callable, which pickle cannot save while the
        proxy holds the class's name, so that reduction too must go through the rebuild helper.

        """
        error = _build_in_place(lambda: InPlaceError("disk full", 28))

        restored = pickle.loads(pickle.dumps(error, protocol=protocol))  # noqa: S301 - payload produced in this test

        assert (type(restored) is type(error), restored.args) == (True, ("disk full", 28))

    def test_own_reduce_is_used(self) -> None:
        """A class that defines its own ``__reduce__`` keeps pickling through it.

        The registered reducer passes a reduction that does not call the class on unchanged, so the class's own
        reduction decides the payload; this one pickles as a plain dict.

        """
        instance = _build_in_place(lambda: InPlaceOwnReduce(5))

        restored = pickle.loads(pickle.dumps(instance))  # noqa: S301 - payload produced in this test

        assert restored == {"level": 5}

    @pytest.mark.parametrize(
        "alias",
        [
            pytest.param(DeprecatedEnum, id="enum-own-reduce-ex"),
            pytest.param(RedirectedDataClass, id="redirect-to-target"),
        ],
    )
    def test_registers_no_reducer(self, alias: Any) -> None:  # noqa: ANN401
        """No reducer is registered for a class deciding ``__reduce_ex__`` itself or for a redirect to another class.

        A class overriding ``__reduce_ex__`` (every ``Enum``) must keep its own pickling, because the dispatch table
        would take precedence over it, and a redirect alias builds instances of its target, so the wrapped class needs
        no reducer at all.

        """
        assert alias.__wrapped__ not in copyreg.dispatch_table

    def test_subclass_is_unaffected(self) -> None:
        """A subclass of the wrapped class gets no reducer of its own: ``copyreg`` dispatch is keyed on the exact type.

        A subclass defined under its own name pickles by reference through the normal path, so registering the
        deprecated class must not leak into it.

        """
        wrapped_class = type(_build_in_place(lambda: InPlaceSettings(level=1)))
        child = type("InPlaceChild", (wrapped_class,), {})

        assert (wrapped_class in copyreg.dispatch_table, child in copyreg.dispatch_table) == (True, False)
