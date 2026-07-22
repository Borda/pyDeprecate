"""Integration tests for deprecated_instance."""

import warnings
from collections.abc import Callable
from typing import Any

import pytest

from deprecate import deprecated_instance
from tests.collection_deprecate import depr_config_dict, depr_config_dict_read_only
from tests.collection_targets import ManagedResource


class TestInstanceProxy:
    """Behaviour of deprecated_instance() proxy objects."""

    def test_name_auto_inferred_from_type(self) -> None:
        """When name is omitted, the type name is used in the warning."""
        proxy = deprecated_instance({"k": "v"}, deprecated_in="1.0", remove_in="2.0")
        with pytest.warns(FutureWarning, match=r"The `dict` was deprecated since v1\.0"):
            _ = proxy["k"]

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
        with pytest.warns(FutureWarning, match=r"The `my_list` was deprecated since v1\.5") as rec:
            iter(proxy)
        assert "1.5" in str(rec.list[0].message)
        assert "3.0" in str(rec.list[0].message)

    def test_iter_warns(self) -> None:
        """__iter__ emits a warning and forwards iteration (dict type)."""
        proxy = deprecated_instance({"a": 1, "b": 2}, name="d", deprecated_in="1.0", remove_in="2.0")
        with pytest.warns(FutureWarning, match=r"The `d` was deprecated since v1\.0"):
            keys = list(proxy)
        assert set(keys) == {"a", "b"}

    def test_collection_config_dict_warns_on_read(self) -> None:
        """depr_config_dict from collection_deprecate emits FutureWarning on read."""
        with pytest.warns(FutureWarning, match=r"The `dict` was deprecated since v1\.0"):
            val = depr_config_dict["threshold"]
        assert val == 0.5


class TestReadOnlyMode:
    """Write protection when read_only=True."""

    @pytest.mark.parametrize(
        "mutation",
        [
            pytest.param(lambda proxy: proxy.__setitem__("x", 99), id="setitem"),
            pytest.param(lambda proxy: proxy.__delitem__("x"), id="delitem"),
            pytest.param(lambda proxy: setattr(proxy, "new_attr", 42), id="setattr"),
        ],
    )
    def test_mutations_raise(self, mutation: Callable[[object], None]) -> None:
        """Read-only proxy should reject item assignment, item deletion, and attribute assignment."""
        proxy = deprecated_instance({"x": 1}, name="d", deprecated_in="1.0", remove_in="2.0", read_only=True)
        with pytest.raises(AttributeError, match="read-only"):
            mutation(proxy)

    def test_collection_read_only_blocks_write(self) -> None:
        """Shared read-only fixture from test collection should enforce write protection consistently across tests."""
        with pytest.raises(AttributeError, match="read-only"):
            depr_config_dict_read_only["threshold"] = 99.0


class TestInstanceProtocolForwarding:
    """deprecated_instance forwards operator and context-manager protocols to the wrapped object."""

    def test_arithmetic_operator_forwards_and_warns(self) -> None:
        """A deprecated numeric constant stays usable in arithmetic and warns once when so used.

        A project retiring a public integer constant wraps it with ``deprecated_instance``; callers writing
        ``LEGACY_COUNT + 5`` must still get the arithmetic result while being nudged with a single FutureWarning.
        """
        proxy: Any = deprecated_instance(10, name="count", deprecated_in="1.0", remove_in="2.0")
        with pytest.warns(FutureWarning, match=r"The `count` was deprecated since v1\.0"):
            total = proxy + 5
        assert total == 15

    def test_context_manager_forwards(self) -> None:
        """A deprecated resource handle drives its ``with`` block, entering and exiting the wrapped object.

        Wrapping a legacy resource in ``deprecated_instance`` must not break ``with resource:`` usage during the
        migration window; both context transitions reach the underlying object.
        """
        resource = ManagedResource()
        proxy = deprecated_instance(resource, name="res", deprecated_in="1.0", remove_in="2.0", stream=None)
        with proxy:
            pass
        assert (resource.entered, resource.exited) == (True, True)


class TestTemplateMgsAliasOnDeprecatedInstanceEntryPoint:
    """The deprecated ``template_mgs`` alias resolves through the ``deprecated_instance`` entry point.

    A maintainer who never migrated off the old, typo'd ``template_mgs`` spelling reaches for the strict
    ``deprecated_instance`` factory directly — this must keep working end-to-end, not just through the
    isolated ``_resolve_message_template_alias`` unit test.
    """

    def test_alias_alone_warns_and_resolves(self) -> None:
        """Supplying only ``template_mgs`` warns ``FutureWarning`` and is adopted as ``message_template``."""
        with pytest.warns(FutureWarning, match="`template_mgs` is deprecated"):
            proxy = deprecated_instance(
                {"k": 1},
                name="legacy_template_mgs_obj",
                deprecated_in="1.0",
                remove_in="2.0",
                template_mgs="Alias notice for `%(source_name)s`.",
            )
        dep = object.__getattribute__(proxy, "__deprecated__")
        assert dep.message_template == "Alias notice for `%(source_name)s`."

    def test_alias_and_message_template_together_raises(self) -> None:
        """Supplying both ``template_mgs`` and ``message_template`` raises ``TypeError`` — no silent merge."""
        with pytest.raises(TypeError, match="pass only one"):
            deprecated_instance(
                {"k": 1},
                name="legacy_template_mgs_obj",
                deprecated_in="1.0",
                remove_in="2.0",
                message_template="Canonical notice.",
                template_mgs="Legacy notice.",
            )
