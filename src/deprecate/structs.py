"""Deprecated instance proxy for data structures and objects."""

from typing import Any, Callable, Iterator, Optional, TypeVar
from warnings import warn

T = TypeVar("T")


class DeprecatedStruct:
    """A proxy wrapper that intercepts access to an object and issues deprecation warnings.

    Optionally restricts modifications when `read_only=True`.
    Can also be used as a decorator for classes.
    """

    def __init__(
        self,
        source: Optional[Any] = None,
        target: Optional[Any] = None,
        name: Optional[str] = None,
        deprecated_in: str = "",
        remove_in: str = "",
        read_only: bool = False,
        stream: Optional[Callable] = warn,
        num_warns: int = 1,
    ) -> None:
        object.__setattr__(self, "_DeprecatedStruct__source", source)
        object.__setattr__(self, "_DeprecatedStruct__target", target)
        object.__setattr__(self, "_DeprecatedStruct__name", name)
        object.__setattr__(self, "_DeprecatedStruct__deprecated_in", deprecated_in)
        object.__setattr__(self, "_DeprecatedStruct__remove_in", remove_in)
        object.__setattr__(self, "_DeprecatedStruct__read_only", read_only)
        object.__setattr__(self, "_DeprecatedStruct__stream", stream)
        object.__setattr__(self, "_DeprecatedStruct__num_warns", num_warns)
        object.__setattr__(self, "_DeprecatedStruct__warned", 0)

    def __warn(self) -> None:
        stream = object.__getattribute__(self, "_DeprecatedStruct__stream")
        if not stream:
            return

        num_warns = object.__getattribute__(self, "_DeprecatedStruct__num_warns")
        warned = object.__getattribute__(self, "_DeprecatedStruct__warned")

        if num_warns < 0 or warned < num_warns:
            name = object.__getattribute__(self, "_DeprecatedStruct__name")
            dep_in = object.__getattribute__(self, "_DeprecatedStruct__deprecated_in")
            rem_in = object.__getattribute__(self, "_DeprecatedStruct__remove_in")
            target = object.__getattribute__(self, "_DeprecatedStruct__target")

            if target is not None:
                target_name = getattr(target, "__name__", str(target))
                msg = f"The `{name}` was deprecated since v{dep_in} in favor of `{target_name}`. It will be removed in v{rem_in}."
            else:
                msg = f"The `{name}` was deprecated since v{dep_in}. It will be removed in v{rem_in}."

            if stream is warn:
                stream(msg, category=FutureWarning, stacklevel=3)
            else:
                stream(msg)

            object.__setattr__(self, "_DeprecatedStruct__warned", warned + 1)

    def __check_read_only(self) -> None:
        if object.__getattribute__(self, "_DeprecatedStruct__read_only"):
            raise RuntimeError("You can read legacy state, but updates are no longer supported—migrate now!")

    def __get_active(self) -> Any:
        source = object.__getattribute__(self, "_DeprecatedStruct__source")
        target = object.__getattribute__(self, "_DeprecatedStruct__target")
        return target if target is not None else source

    def __getattr__(self, item: str) -> Any:
        self.__warn()
        return getattr(self.__get_active(), item)

    def __setattr__(self, key: str, value: Any) -> None:
        self.__check_read_only()
        self.__warn()
        setattr(self.__get_active(), key, value)

    def __delattr__(self, item: str) -> None:
        self.__check_read_only()
        self.__warn()
        delattr(self.__get_active(), item)

    def __getitem__(self, key: Any) -> Any:
        self.__warn()
        return self.__get_active()[key]

    def __setitem__(self, key: Any, value: Any) -> None:
        self.__check_read_only()
        self.__warn()
        self.__get_active()[key] = value

    def __delitem__(self, key: Any) -> None:
        self.__check_read_only()
        self.__warn()
        del self.__get_active()[key]

    def __iter__(self) -> Iterator:
        self.__warn()
        return iter(self.__get_active())

    def __len__(self) -> int:
        self.__warn()
        return len(self.__get_active())

    def __contains__(self, item: Any) -> bool:
        self.__warn()
        return item in self.__get_active()

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        source = object.__getattribute__(self, "_DeprecatedStruct__source")
        target = object.__getattribute__(self, "_DeprecatedStruct__target")
        
        if source is None:
            # Acts as a decorator
            if len(args) != 1 or kwargs:
                raise TypeError("When used as a decorator, must be called with a single target.")
            
            new_source = args[0]
            name = object.__getattribute__(self, "_DeprecatedStruct__name") or getattr(new_source, "__name__", "object")
            
            return type(self)(
                source=new_source,
                target=target,
                name=name,
                deprecated_in=object.__getattribute__(self, "_DeprecatedStruct__deprecated_in"),
                remove_in=object.__getattribute__(self, "_DeprecatedStruct__remove_in"),
                read_only=object.__getattribute__(self, "_DeprecatedStruct__read_only"),
                stream=object.__getattribute__(self, "_DeprecatedStruct__stream"),
                num_warns=object.__getattribute__(self, "_DeprecatedStruct__num_warns"),
            )
            
        self.__warn()
        return self.__get_active()(*args, **kwargs)


def deprecated_instance(
    source: Any,
    target: Optional[Any] = None,
    name: Optional[str] = None,
    deprecated_in: str = "",
    remove_in: str = "",
    read_only: bool = False,
    stream: Optional[Callable] = warn,
    num_warns: int = 1,
) -> Any:
    """Wrap an instance to issue deprecation warnings on access, optionally forwarding to a new target.

    Args:
        source: The legacy object to wrap (e.g., dict, list, set, custom object instance).
        target: Optional new object to redirect interactions to instead of the source.
        name: Name of the object to display in the warning message. If None, falls
            back to `source.__name__` or "object".
        deprecated_in: Version when the object was deprecated (e.g., "1.0").
        remove_in: Version when the object will be removed (e.g., "2.0").
        read_only: If True, attempts to modify the object will raise a RuntimeError.
        stream: Callable to emit the warning. Defaults to warnings.warn.
        num_warns: Number of times to warn. -1 for infinite. Defaults to 1.

    Returns:
        A proxy object that behaves like the active object but warns on access.

    Example:
        >>> old_cfg = {"threshold": 0.5, "enabled": True}
        >>> new_cfg = {"threshold": 0.5, "enabled": True}
        >>> cfg = deprecated_instance(
        ...     old_cfg, target=new_cfg, name="config_dict", deprecated_in="1.0", remove_in="2.0"
        ... )
        >>> val = cfg["threshold"]  # Warns
    """
    target_name = name or getattr(source, "__name__", "object")
    return DeprecatedStruct(
        source=source,
        target=target,
        name=target_name,
        deprecated_in=deprecated_in,
        remove_in=remove_in,
        read_only=read_only,
        stream=stream,
        num_warns=num_warns,
    )
