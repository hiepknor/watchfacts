from app.searching import search_result as _impl
import sys as _sys

_sys.modules[__name__] = _impl
