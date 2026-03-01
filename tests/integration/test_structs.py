"""Unit tests for the deprecated_instance proxy utility."""

import warnings

import pytest

from deprecate import deprecated_instance
from tests.collection_deprecate import (
    Colors,
    OldColors,
    Settings,
    depr_config_dict,
    depr_read_only_dict,
    depr_redirect_dict,
)
from tests.collection_targets import (
    NewCls,
    TimerDecorator,
    base_sum_kwargs,
)


class TestDeprecatedInstance:
    def test_dict_read_warning(self) -> None:
        with pytest.warns(FutureWarning, match="The `depr_config_dict` was deprecated since v1.0. It will be removed in v2.0."):
            assert depr_config_dict["threshold"] == 0.5

    def test_dict_read_only(self) -> None:
        with pytest.raises(RuntimeError, match="migrate now!"):
            depr_read_only_dict["enabled"] = False

        with pytest.raises(RuntimeError, match="migrate now!"):
            del depr_read_only_dict["enabled"]

    def test_list_proxy(self) -> None:
        lst = [1, 2, 3]
        proxy = deprecated_instance(lst, name="my_list", deprecated_in="1.0", remove_in="2.0", num_warns=2)

        with pytest.warns(FutureWarning):
            assert len(proxy) == 3

        with pytest.warns(FutureWarning):
            assert 2 in proxy

        with warnings.catch_warnings():
            warnings.simplefilter("error")
            assert proxy[0] == 1

    def test_enum_proxy(self) -> None:
        # Proxying an existing enum instance
        proxy = deprecated_instance(Colors.RED, name="ColorRed", deprecated_in="1.0", remove_in="2.0")

        with pytest.warns(FutureWarning, match="The `ColorRed` was deprecated"):
            assert proxy.value == 1

    def test_custom_object(self) -> None:
        obj = NewCls(c=42)
        proxy = deprecated_instance(obj, name="dummy_obj", deprecated_in="1.0", remove_in="2.0")

        with pytest.warns(FutureWarning):
            assert proxy.my_c == 42

    def test_callable_proxy(self) -> None:
        proxy = deprecated_instance(base_sum_kwargs, name="my_func_proxy", deprecated_in="1.0", remove_in="2.0")
        with pytest.warns(FutureWarning):
            assert proxy(a=1, b=2) == 3

    def test_decorator_proxy(self) -> None:
        # Deprecate the decorator instance itself
        depr_decorator = deprecated_instance(TimerDecorator, name="TimerDecorator", deprecated_in="1.0", remove_in="2.0")

        # Use the deprecated decorator
        with pytest.warns(FutureWarning, match="The `TimerDecorator` was deprecated"):
            @depr_decorator
            def add_two(x: int) -> int:
                return x + 2
                
        # Call the decorated function - shouldn't warn again unless the wrapper was also deprecated
        assert add_two(5) == 7

    def test_dict_redirection(self) -> None:
        with pytest.warns(FutureWarning, match=r"The `depr_redirect_dict` was deprecated.*in favor of.*"):
            assert depr_redirect_dict["a"] == 2
        
        # Modification should route to target
        with pytest.warns(FutureWarning):
            depr_redirect_dict["b"] = 3


class TestDeprecatedStruct:
    def test_enum_decorator(self) -> None:
        with pytest.warns(FutureWarning, match="The `Colors` was deprecated"):
            val = Colors.RED.value
        assert val == 1

    def test_dataclass_decorator(self) -> None:
        with pytest.warns(FutureWarning, match="The `Settings` was deprecated"):
            cfg = Settings("localhost", 8080)

        assert cfg.host == "localhost"
        assert cfg.port == 8080

    def test_enum_redirection(self) -> None:
        with pytest.warns(FutureWarning, match="The `OldColors` was deprecated since v1.0 in favor of `TargetColors`. It will be removed in v2.0."):
            val = OldColors.RED.value
        
        # Should redirect to TargetColors's RED which is 10
        assert val == 10

