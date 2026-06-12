from app.runtime import mcp_server as _impl
import sys as _sys

if __name__ == "__main__":
    _impl.main()
else:
    _sys.modules[__name__] = _impl
