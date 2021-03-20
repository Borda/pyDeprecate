# pyDeprecate

**Simple tooling for marking deprecated functions or classes and re-routing to the new successors' instance.**

[![PyPI - Python Version](https://img.shields.io/pypi/pyversions/pyDeprecate)](https://pypi.org/project/pyDeprecate/)
[![PyPI Status](https://badge.fury.io/py/pyDeprecate.svg)](https://badge.fury.io/py/pyDeprecate)
[![PyPI Status](https://pepy.tech/badge/pyDeprecate)](https://pepy.tech/project/pyDeprecate)
[![license](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://github.com/Borda/pyDeprecate/blob/master/LICENSE)

[![CI testing](https://github.com/Borda/pyDeprecate/actions/workflows/ci_testing.yml/badge.svg?branch=main&event=push)](https://github.com/Borda/pyDeprecate/actions/workflows/ci_testing.yml)
[![Code formatting](https://github.com/Borda/pyDeprecate/actions/workflows/code-format.yml/badge.svg?branch=main&event=push)](https://github.com/Borda/pyDeprecate/actions/workflows/code-format.yml)
[![codecov](https://codecov.io/gh/Borda/pyDeprecate/branch/main/graph/badge.svg?token=BG7RQ86UJA)](https://codecov.io/gh/Borda/pyDeprecate)
[![CodeFactor](https://www.codefactor.io/repository/github/borda/pydeprecate/badge)](https://www.codefactor.io/repository/github/borda/pydeprecate)

<!--
[![Language grade: Python](https://img.shields.io/lgtm/grade/python/g/Borda/pyDeprecate.svg?logo=lgtm&logoWidth=18)](https://lgtm.com/projects/g/Borda/pyDeprecate/context:python)
-->

---

The common use-case is moving your functions across codebase or outsourcing some functionalities to new packages.
For most of these cases, you want to hold some compatibility, so you cannot simply remove past function, and also for some time you want to warn users that functionality they have been using is moved and not it is deprecated in favor of another function (which shall be used instead) and soon it will be removed completely.

Another good aspect is to do not overwhelm a user with too many warnings, so per function/class, this warning is raised only N times in the preferable stream (warning, logger, etc.).

## Installation

Simple installation from PyPI:
```bash
pip install pyDeprecate
```

<details>
  <summary>Other installations</summary>

  Simply install with pip from source:
  ```bash
  pip install https://github.com/Borda/pyDeprecate/archive/main.zip
  ```

</details>

## Use-cases

The functionality is kept simple and all default shall be reasonable, but still you can do extra customization such as:

* define user warning message and preferable stream
* extended argument mapping to target function/method

### Simple function forwarding

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

### Advanced argument mapping

Another more complex example is using argument mapping is:
```python
import logging
from sklearn.metrics import accuracy_score
from deprecate import deprecated

@deprecated(
  # use standard sklearn accuracy implementation
  target=accuracy_score,
  # custom warning stream
  stream=logging.warning,
  # custom message template
  template_mgs="`%(source_name)s` was deprecated, use `%(target_path)s`",
  # as target args are different, define mapping
  args_mapping={'preds': 'y_pred', 'target': 'y_true', 'blabla': None}
)
def depr_accuracy(preds: list, target: list, blabla: float) -> float:
    """
    My deprecated function which is mapping to sklearn accuracy.
    """
    pass  # or you can just place docstring as one above

# call this function will raise deprecation warning:
# WARNING:root:`depr_accuracy` was deprecated, use `sklearn.metrics.accuracy_score`
depr_accuracy([1, 0, 1, 2], [0, 1, 1, 2], 1.23)
# returns: 0.5
```

### Deprecation warning only

Base use-case with no forwarding and just raising warning :

```python
from deprecate import deprecated

@deprecated(target=None, deprecated_in="0.1", remove_in="0.5")
def my_sum(a: int, b: int = 5) -> int:
    """
    My deprecated function which still has to have implementation.
    """
    return a + b

# call this function will raise deprecation warning
my_sum(1, 2)
# returns: 3
```


### Class deprecation

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
