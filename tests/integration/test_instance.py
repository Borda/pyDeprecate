"""Integration tests for deprecated_instance."""

import warnings

import pytest

from deprecate import deprecated_instance
from deprecate.proxy import _DeprecatedProxy
from tests.collection_deprecate import (
    depr_config_dict,
    depr_config_dict_read_only,
)


class TestDemoUseCases:
    """Canonical examples from issue #109."""

    def test_dict_proxy_warns_on_read(self) -> None:
        """Deprecated dict proxy: read warns, value is returned correctly."""
        old_cfg = {"threshold": 0.5, "enabled": True}
        cfg = deprecated_instance(old_cfg, name="config_dict", deprecated_in="1.0", remove_in="2.0")

        with pytest.warns(FutureWarning, match="config_dict"):
            val = cfg["threshold"]

        assert val == 0.5

    def test_read_only_allows_reads_blocks_writes(self) -> None:
        """Read-only mode: reads succeed (with warning), writes raise AttributeError."""
        old_cfg = {"threshold": 0.5, "enabled": True}
        cfg = deprecated_instance(
            old_cfg,
            name="config_dict",
            deprecated_in="1.0",
            remove_in="2.0",
            read_only=True,
        )

        with pytest.warns(FutureWarning):
            val = cfg["threshold"]
        assert val == 0.5

        with pytest.raises(AttributeError, match="read-only"):
            cfg["enabled"] = False


class TestInstanceProxy:
    """Behaviour of deprecated_instance() proxy objects."""

    def test_returns_deprecated_proxy_instance(self) -> None:
        """deprecated_instance returns a _DeprecatedProxy."""
        proxy = deprecated_instance({}, name="x", deprecated_in="1.0", remove_in="2.0")
        assert isinstance(proxy, _DeprecatedProxy)

    def test_name_auto_inferred_from_type(self) -> None:
        """When name is omitted, the type name is used in the warning."""
        proxy = deprecated_instance({"k": "v"}, deprecated_in="1.0", remove_in="2.0")
        with pytest.warns(FutureWarning, match="dict"):
            _ = proxy["k"]

    def test_stream_none_produces_no_warnings(self) -> None:
        """stream=None produces no warnings."""
        proxy = deprecated_instance({"k": "v"}, name="x", deprecated_in="1.0", remove_in="2.0", stream=None)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            _ = proxy["k"]
        assert not caught

    def test_num_warns_one_warns_once(self) -> None:
        """num_warns=1 emits warning only once."""
        proxy = deprecated_instance([1, 2, 3], name="lst", deprecated_in="1.0", remove_in="2.0", num_warns=1)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            _ = proxy[0]
            _ = proxy[0]
        assert len(caught) == 1

    def test_num_warns_infinite(self) -> None:
        """num_warns=-1 emits warning on every access."""
        proxy = deprecated_instance([1, 2], name="lst", deprecated_in="1.0", remove_in="2.0", num_warns=-1)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            _ = proxy[0]
            _ = proxy[0]
        assert len(caught) == 2

    def test_warning_message_contains_name_and_versions(self) -> None:
        """Warning message contains name and version info."""
        proxy = deprecated_instance([], name="my_list", deprecated_in="1.5", remove_in="3.0")
        with pytest.warns(FutureWarning, match="my_list") as rec:
            iter(proxy)
        assert "1.5" in str(rec.list[0].message)
        assert "3.0" in str(rec.list[0].message)

    def test_len_no_warning(self) -> None:
        """__len__ delegates without warning."""
        proxy = deprecated_instance({"a": 1, "b": 2}, name="d", deprecated_in="1.0", remove_in="2.0")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            n = len(proxy)
        assert n == 2
        assert not caught

    def test_contains_no_warning(self) -> None:
        """__contains__ delegates without warning."""
        proxy = deprecated_instance({"x": 1}, name="d", deprecated_in="1.0", remove_in="2.0")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            found = "x" in proxy
        assert found
        assert not caught

    def test_iter_warns(self) -> None:
        """__iter__ emits a warning and forwards iteration."""
        proxy = deprecated_instance({"a": 1, "b": 2}, name="d", deprecated_in="1.0", remove_in="2.0")
        with pytest.warns(FutureWarning, match="d"):
            keys = list(proxy)
        assert set(keys) == {"a", "b"}

    def test_getattr_warns(self) -> None:
        """Attribute delegation emits warning."""
        proxy = deprecated_instance({"a": 1}, name="d", deprecated_in="1.0", remove_in="2.0")
        with pytest.warns(FutureWarning):
            result = proxy.get("a", 99)
        assert result == 1

    def test_repr_and_str_no_warning(self) -> None:
        """__repr__ and __str__ delegate without warning."""
        inner = {"x": 1}
        proxy = deprecated_instance(inner, name="d", deprecated_in="1.0", remove_in="2.0")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            r = repr(proxy)
            s = str(proxy)
        assert r == repr(inner)
        assert s == str(inner)
        assert not caught

    def test_bool_no_warning(self) -> None:
        """__bool__ delegates without warning."""
        proxy_empty = deprecated_instance({}, name="d", deprecated_in="1.0", remove_in="2.0")
        proxy_full = deprecated_instance({"k": 1}, name="d", deprecated_in="1.0", remove_in="2.0")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            assert not bool(proxy_empty)
            assert bool(proxy_full)
        assert not caught

    def test_eq_with_original_object(self) -> None:
        """Proxy equality matches the wrapped object."""
        inner = {"k": "v"}
        proxy = deprecated_instance(inner, name="d", deprecated_in="1.0", remove_in="2.0", stream=None)
        assert proxy == inner
        proxy2 = deprecated_instance(inner, name="d", deprecated_in="1.0", remove_in="2.0", stream=None)
        assert proxy is not proxy2

    def test_hash_matches_inner(self) -> None:
        """hash(proxy) equals hash(wrapped object) for hashable types."""
        inner = (1, 2, 3)
        proxy = deprecated_instance(inner, name="t", deprecated_in="1.0", remove_in="2.0", stream=None)
        assert hash(proxy) == hash(inner)

    def test_collection_config_dict_warns_on_read(self) -> None:
        """depr_config_dict from collection_deprecate emits FutureWarning on read."""
        with pytest.warns(FutureWarning, match="dict"):
            val = depr_config_dict["threshold"]
        assert val == 0.5


class TestReadOnlyMode:
    """Write protection when read_only=True."""

    def test_setitem_raises(self) -> None:
        """read_only=True blocks __setitem__."""
        proxy = deprecated_instance({"x": 1}, name="d", deprecated_in="1.0", remove_in="2.0", read_only=True)
        with pytest.raises(AttributeError, match="read-only"):
            proxy["x"] = 99

    def test_delitem_raises(self) -> None:
        """read_only=True blocks __delitem__."""
        proxy = deprecated_instance({"x": 1}, name="d", deprecated_in="1.0", remove_in="2.0", read_only=True)
        with pytest.raises(AttributeError, match="read-only"):
            del proxy["x"]

    def test_setattr_raises(self) -> None:
        """read_only=True blocks attribute mutation."""
        proxy = deprecated_instance({"x": 1}, name="d", deprecated_in="1.0", remove_in="2.0", read_only=True)
        with pytest.raises(AttributeError, match="read-only"):
            proxy.new_attr = 42

    def test_writable_allows_mutation_no_warning(self) -> None:
        """read_only=False (default) allows mutation; writes do not warn."""
        inner = {"x": 1}
        proxy = deprecated_instance(inner, name="d", deprecated_in="1.0", remove_in="2.0", stream=None)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            proxy["x"] = 99
        assert inner["x"] == 99
        assert not caught

    def test_delitem_removes_from_source(self) -> None:
        """Removing proxy[key] removes the key from the source dict."""
        inner = {"x": 1, "y": 2}
        proxy = deprecated_instance(inner, name="d", deprecated_in="1.0", remove_in="2.0", stream=None)
        del proxy["x"]
        assert "x" not in inner

    def test_collection_read_only_blocks_write(self) -> None:
        """depr_config_dict_read_only raises on write attempt."""
        with pytest.raises(AttributeError, match="read-only"):
            depr_config_dict_read_only["threshold"] = 99.0
