"""Demo: deprecated_module() — all three modes.

This script serves as the API contract for the deprecated_module feature. Must FAIL against current code (feature
doesn't exist yet). Promoted to a pytest test once implementation is complete.

"""

import sys
import types
import warnings


def _check(cond: bool, msg: str) -> None:
    if not cond:
        raise RuntimeError(msg)


# ── Mode 1: in-place warn ────────────────────────────────────────────────────
def _demo_mode1() -> None:
    """Mode 1 — in-place warn; module stays at original path."""
    old_mod = types.ModuleType("fake_old_math")
    sys.modules["fake_old_math"] = old_mod

    import deprecate

    deprecate.deprecated_module(
        "fake_old_math",
        deprecated_in="1.0",
        remove_in="2.0",
        message="Use new_math instead.",
    )

    mod = sys.modules["fake_old_math"]
    _check(hasattr(mod, "__deprecated__"), "Module must have __deprecated__ DeprecationConfig")

    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        getattr(mod, "some_attr", None)

    _check(len(w) == 1, f"Expected 1 warning, got {len(w)}")
    _check(issubclass(w[0].category, FutureWarning), f"Expected FutureWarning, got {w[0].category}")
    _check("deprecated" in str(w[0].message).lower(), f"Warning missing 'deprecated': {w[0].message}")


# ── Mode 2: redirect ─────────────────────────────────────────────────────────
def _demo_mode2() -> None:
    """Mode 2 — redirect; attribute access forwarded to target module."""
    new_mod = types.ModuleType("fake_new_utils")
    new_mod.add = lambda x, y: x + y  # type: ignore[attr-defined]
    sys.modules["fake_new_utils"] = new_mod

    old_mod = types.ModuleType("fake_old_utils")
    sys.modules["fake_old_utils"] = old_mod

    import deprecate

    deprecate.deprecated_module(
        "fake_old_utils",
        target=new_mod,
        deprecated_in="1.0",
        remove_in="2.0",
    )

    mod = sys.modules["fake_old_utils"]

    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        result = mod.add(2, 3)

    _check(result == 5, f"Expected 5, got {result}")
    _check(len(w) == 1, f"Expected 1 warning, got {len(w)}")
    _check(issubclass(w[0].category, FutureWarning), f"Expected FutureWarning, got {w[0].category}")


# ── Audit discovers the module ───────────────────────────────────────────────
def _demo_audit() -> None:
    """find_deprecation_wrappers must discover a deprecated module."""
    import deprecate

    old_mod = types.ModuleType("fake_audit_old")
    sys.modules["fake_audit_old"] = old_mod

    deprecate.deprecated_module(
        "fake_audit_old",
        deprecated_in="1.0",
        remove_in="2.0",
        message="Use fake_audit_new.",
    )

    mod = sys.modules["fake_audit_old"]
    results = deprecate.find_deprecation_wrappers(mod, recursive=False)
    _check(len(results) == 1, f"Expected 1 result, got {len(results)}: {results}")
    _check(results[0].deprecated_info.deprecated_in == "1.0", "Wrong deprecated_in")


if __name__ == "__main__":
    _demo_mode1()
    print("Mode 1: OK")
    _demo_mode2()
    print("Mode 2: OK")
    _demo_audit()
    print("Audit: OK")
    print("All demos passed.")
