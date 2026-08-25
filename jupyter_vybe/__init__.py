"""Jupyter Vybe — fine-grained AI code generation in Jupyter notebooks.

The server extension exists only to host agent adapters (subprocesses, HTTP
backends) that a browser cannot own itself. It never speaks to a kernel: this
package must not import jupyter_client, ipykernel, or nbclient, and a test
enforces that (see tests/test_no_kernel_imports.py).
"""

try:
    from ._version import __version__
except ImportError:
    # Fallback when using the package in dev mode without installing
    # in editable mode with pip.
    import warnings

    warnings.warn("Importing 'jupyter_vybe' outside a proper installation.")
    __version__ = "dev"

from .handlers import setup_handlers


def _jupyter_labextension_paths():
    return [{"src": "labextension", "dest": "jupyter-vybe"}]


def _jupyter_server_extension_points():
    return [{"module": "jupyter_vybe"}]


def _load_jupyter_server_extension(server_app):
    """Register the Vybe REST and WebSocket handlers."""
    setup_handlers(server_app.web_app)
    server_app.log.info("Registered jupyter_vybe server extension")
