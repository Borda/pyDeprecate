"""The documented PEP 702 stacking pattern: static checkers flag the symbol, pyDeprecate alone warns at runtime.

Runtime behaviour is asserted by pytest below. Static behaviour is asserted by mypy through the ``TYPE_CHECKING`` block
at the bottom of this module (checked by the ``typing`` pre-commit hook, never executed).

"""

import warnings
from typing import TYPE_CHECKING, Any, Callable

import pytest

import tests.collection_pep702 as pep702_fixtures
from deprecate import deprecated, deprecated_callable


class TestStackedPep702Runtime:
    """A PEP 702 decorator stacked with ``category=None`` adds no runtime behaviour of its own."""

    @pytest.mark.parametrize(
        ("call", "expected"),
        [
            pytest.param(lambda: pep702_fixtures.stacked_callable(2), 4, id="function-forwards"),  # type: ignore[deprecated]
            pytest.param(lambda: pep702_fixtures.StackedAlias(3).doubled(), 6, id="class-alias-forwards"),  # type: ignore[deprecated]
            pytest.param(lambda: pep702_fixtures.StackedMembers().legacy_method(5), 5, id="method"),  # type: ignore[deprecated]
            pytest.param(lambda: pep702_fixtures.StackedMembers().legacy_value, 1, id="property"),  # type: ignore[deprecated]
            pytest.param(lambda: pep702_fixtures.StackedMembers.legacy_static(7), 7, id="staticmethod"),  # type: ignore[deprecated]
        ],
    )
    def test_emits_exactly_one_future_warning(self, call: Callable[[], Any], expected: int) -> None:
        """Each stacked symbol still works and emits only pyDeprecate's ``FutureWarning`` — never a second warning.

        A library adds the PEP 702 decorator purely so editors and CI type checks flag the symbol. With
        ``category=None`` the decorator must not add its own ``DeprecationWarning``: callers would otherwise see every
        deprecation twice, once per decorator.

        """
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = call()
        assert (result, [w.category for w in caught]) == (expected, [FutureWarning])


if TYPE_CHECKING:
    # mypy has `enable_error_code = ["deprecated"]` and `warn_unused_ignores = true` for this module only
    # (`[tool.mypy]` overrides in `pyproject.toml`). Every line below must be flagged as deprecated; if a documented
    # spelling stops being recognised, its ignore becomes unused and the typing hook fails.
    pep702_fixtures.stacked_callable(1)  # type: ignore[deprecated]
    pep702_fixtures.StackedAlias(1)  # type: ignore[deprecated]
    pep702_fixtures.StackedMembers().legacy_method(1)  # type: ignore[deprecated]
    _ = pep702_fixtures.StackedMembers().legacy_value  # type: ignore[deprecated]
    pep702_fixtures.StackedMembers.legacy_static(1)  # type: ignore[deprecated]

    # Invalid non-callable sources must be rejected statically as well as at decoration time.
    # warn_unused_ignores makes either line fail when its decorator's type variable becomes unbounded.
    deprecated(deprecated_in="1.0", remove_in="2.0")(42)  # type: ignore[call-overload]
    deprecated_callable(deprecated_in="1.0", remove_in="2.0")(42)  # type: ignore[type-var]
