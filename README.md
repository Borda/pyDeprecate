# pyDeprecate

[![CI testing](https://github.com/Borda/pyDeprecate/actions/workflows/ci_testing.yml/badge.svg?event=push)](https://github.com/Borda/pyDeprecate/actions/workflows/ci_testing.yml)
[![Code formatting](https://github.com/Borda/pyDeprecate/actions/workflows/code-format.yml/badge.svg?event=push)](https://github.com/Borda/pyDeprecate/actions/workflows/code-format.yml)
[![codecov](https://codecov.io/gh/Borda/pyDeprecate/branch/main/graph/badge.svg?token=BG7RQ86UJA)](https://codecov.io/gh/Borda/pyDeprecate)

Simple tooling for marking deprecated function or classes and re-routing to the new successors instance.

The common use-case is moving your functions across codebase or outsourcing some functionalities to new packages.
For most of these cases you want to hold some compatibility, so you cannot simply remove past function,
 and also for some time you want to warn users that functionality they have been using is moved
 and not it in deprecated in favour of another function (which shall be used instead) and soon it will be removed completely.

Another good aspect is to do not overwhelm user with to many warning, so per function/class this warning is raised only once.

## Installation

Simply install with pip from source:
```bash
pip install https://github.com/Borda/pyDeprecate/archive/main.zip
```

## Use-cases

### Functions

It is very straight forward, you forward your function call to new function and all arguments are mapped:

```python
def base_sum(a: int = 0, b: int = 3) -> int:
    """My new function anywhere in codebase or even other package."""
    return a + b

# ---------------------------

from deprecate import deprecated

@deprecated(target=base_sum, deprecated_in="0.1", remove_in="0.5")
def depr_sum(a: int, b: int = 5) -> int:
    """
    My deprecated function which now has empty body
     as all calls are routed to the new function.
    """
    pass  # or you can just place docstring as one above

# call this function will raise deprecation warning
depr_sum(1, 2)
# returns: 3
```

### Classes

This case can be quite complex as you may deprecate just some methods, here we show full class deprecation:

```python
class NewCls:
    """My new class anywhere in the codebase or other package."""

    def __init__(self, c: float, d: str = "abc"):
        self.my_c = c
        self.my_d = d

# ---------------------------

from deprecate import deprecated

class PastCls(NewCls):
    """
    The deprecated class shall be inherited from the successor class
     to hold all methods.
    """

    @deprecated(target=NewCls, deprecated_in="0.2", remove_in="0.4")
    def __init__(self, c: int, d: str = "efg"):
        """
        You place the decorator around __init__ as you want
         to warn user just at the time of creating object.
        """
        pass  # or you can just place docstring as one above

# call this function will raise deprecation warning
inst = PastCls(7)
inst.my_c  # returns: 7
inst.my_d  # returns: "efg"
```

## Contribution

Have you faced this in past or even now, do you have good ideas for improvement, all is welcome! 
