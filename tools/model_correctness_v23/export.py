"""M23-only single traversal of the existing JSON export datatype boundary."""
from __future__ import annotations
import math
import numpy as np

def json_clean(value):
    """Match the legacy JSON value semantics without re-cleaning whole trees.

    The simulator's original objects are read only. Each output occurrence
    is normalized once; arrays/scalars become their ordinary Python values,
    tuples/sets become arrays, keys become strings and nonfinite floats null.
    Unsupported non-JSON primitives retain the legacy serializer error.
    """
    if isinstance(value,dict):return {str(key):json_clean(item) for key,item in value.items()}
    if isinstance(value,(list,tuple,set)):return [json_clean(item) for item in value]
    if isinstance(value,np.ndarray):return json_clean(value.tolist())
    if isinstance(value,np.generic):
        primitive=value.item()
        # Extended NumPy scalar types without an ordinary Python analogue
        # remain unsupported, as in the old serializer, without recursion.
        return primitive if isinstance(primitive,np.generic) else json_clean(primitive)
    if isinstance(value,float) and not math.isfinite(value):return None
    return value
