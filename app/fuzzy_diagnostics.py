from app.searching import fuzzy_diagnostics as _impl
import sys as _sys

_sys.modules[__name__] = _impl
