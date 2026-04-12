"""Runtime dependency loading helpers for Slicer.

Slicer has its own Python runtime. If PYTHONPATH or user site-packages leak in
from another Python installation, binary wheels such as SimpleITK can shadow the
Slicer-compatible package and fail while loading their DLLs. These helpers retry
imports while temporarily ignoring the offending external site-packages entry.
"""

import importlib
import importlib.util
import logging
import os
import sys


_PROXY_ENV_NAMES = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
)
_SOCKS_PROXY_PREFIXES = ("socks://", "socks4://", "socks4a://", "socks5://", "socks5h://")


def import_slicer_dependency(module_name, pip_name=None, install_on_missing=False):
    """Import a dependency, retrying around external Python site-packages leaks."""
    spec = importlib.util.find_spec(module_name)
    try:
        return importlib.import_module(module_name)
    except (ImportError, OSError) as first_error:
        first_origin = _spec_origin(spec)
        offending_entry = _external_sys_path_entry_for(first_origin)
        retry_error = None

        if offending_entry:
            module, retry_error = _retry_without_sys_path_entry(module_name, offending_entry)
            if module is not None:
                logging.warning(
                    "Imported %s after temporarily ignoring external Python path: %s",
                    module_name,
                    offending_entry,
                )
                return module

        if pip_name and install_on_missing:
            _clear_module_tree(module_name)
            try:
                import slicer

                install_target = f"--ignore-installed {pip_name}" if first_origin else pip_name
                _pip_install(slicer, install_target)
                importlib.invalidate_caches()
                if offending_entry:
                    module, retry_error = _retry_without_sys_path_entry(module_name, offending_entry)
                    if module is not None:
                        return module
                return importlib.import_module(module_name)
            except Exception as pip_error:
                retry_error = pip_error

        raise ImportError(
            _format_dependency_error(
                module_name,
                first_error,
                first_origin,
                offending_entry,
                retry_error,
            )
        ) from (retry_error or first_error)


def _retry_without_sys_path_entry(module_name, sys_path_entry):
    original_sys_path = list(sys.path)
    try:
        sys.path[:] = [
            entry
            for entry in sys.path
            if not _same_path(entry or os.getcwd(), sys_path_entry)
        ]
        _clear_module_tree(module_name)
        return importlib.import_module(module_name), None
    except (ImportError, OSError) as error:
        return None, error
    finally:
        sys.path[:] = original_sys_path


def _clear_module_tree(module_name):
    for name in list(sys.modules):
        if name == module_name or name.startswith(module_name + "."):
            sys.modules.pop(name, None)


def _spec_origin(spec):
    if spec is None:
        return None
    origin = getattr(spec, "origin", None)
    if origin:
        return origin
    locations = getattr(spec, "submodule_search_locations", None)
    if locations:
        for location in locations:
            return location
    return None


def _external_sys_path_entry_for(origin):
    if not origin:
        return None
    entry = _matching_sys_path_entry(origin)
    if entry and _is_external_python_site_packages(entry):
        return entry
    return None


def _matching_sys_path_entry(origin):
    origin_abs = _abspath(origin)
    matches = []
    for entry in sys.path:
        entry_abs = _abspath(entry or os.getcwd())
        if _is_path_or_child(origin_abs, entry_abs):
            matches.append((len(entry_abs), entry or os.getcwd()))
    if not matches:
        return None
    return max(matches, key=lambda item: item[0])[1]


def _is_external_python_site_packages(path):
    path_abs = _abspath(path)
    lowered = path_abs.lower()
    if "site-packages" not in lowered and "dist-packages" not in lowered:
        return False
    return not any(_is_path_or_child(path_abs, root) for root in _slicer_root_paths())


def _slicer_root_paths():
    roots = []
    try:
        import slicer

        slicer_file = getattr(slicer, "__file__", None)
        if slicer_file:
            roots.append(os.path.dirname(slicer_file))
        app = getattr(slicer, "app", None)
        for attr_name in (
            "applicationDirPath",
            "slicerHome",
            "launcherExecutableFilePath",
            "temporaryPath",
            "extensionsInstallPath",
        ):
            value = getattr(app, attr_name, None) if app is not None else None
            if callable(value):
                value = value()
            if value:
                roots.append(value if os.path.isdir(value) else os.path.dirname(value))
    except Exception:
        pass
    return [_abspath(path) for path in roots if path]


def _is_path_or_child(path, parent):
    path_abs = _abspath(path)
    parent_abs = _abspath(parent)
    try:
        return os.path.commonpath([path_abs, parent_abs]) == parent_abs
    except ValueError:
        return False


def _same_path(left, right):
    return os.path.normcase(_abspath(left)) == os.path.normcase(_abspath(right))


def _abspath(path):
    return os.path.normcase(os.path.abspath(path))


def _format_dependency_error(
    module_name,
    first_error,
    first_origin,
    offending_entry,
    retry_error,
):
    lines = [
        f"Failed to import required dependency '{module_name}'.",
        f"First import error: {first_error}",
    ]
    if retry_error is not None:
        lines.append(f"Retry error: {retry_error}")
    if first_origin:
        lines.append(f"First matched package: {first_origin}")
    if offending_entry:
        lines.extend(
            [
                f"Slicer appears to be seeing an external Python path: {offending_entry}",
                "Remove PYTHONPATH/PYTHONHOME entries that point to another Python install,",
                "or use PYTHONNOUSERSITE=1 if the path comes from user site-packages,",
                "then restart Slicer and reload this module.",
            ]
        )
    if _has_socks_proxy_env():
        lines.extend(
            [
                f"Detected SOCKS proxy environment variables: {_socks_proxy_env_summary()}",
                "Slicer pip cannot use SOCKS proxies until PySocks is already installed.",
                "The dependency loader temporarily removes SOCKS proxy variables while installing;",
                "if installation still fails, use an HTTP proxy or clear the proxy variables before starting Slicer.",
            ]
        )
    elif pip_name_hint := _pip_name_hint(module_name):
        lines.append(f"Try installing it into Slicer's Python environment: {pip_name_hint}")
    return "\n".join(lines)


def _pip_name_hint(module_name):
    if module_name == "cv2":
        return "slicer.util.pip_install('opencv-python')"
    if module_name == "SimpleITK":
        return "slicer.util.pip_install('SimpleITK')"
    return None


def _pip_install(slicer_module, install_target):
    if not _has_socks_proxy_env():
        slicer_module.util.pip_install(install_target)
        return

    logging.warning(
        "Temporarily disabling SOCKS proxy environment variables for Slicer pip install: %s",
        _socks_proxy_env_summary(),
    )
    removed = {}
    try:
        for name in _PROXY_ENV_NAMES:
            value = os.environ.get(name)
            if value and _is_socks_proxy(value):
                removed[name] = value
                os.environ.pop(name, None)
        slicer_module.util.pip_install(install_target)
    finally:
        os.environ.update(removed)


def _has_socks_proxy_env():
    return any(_is_socks_proxy(os.environ.get(name, "")) for name in _PROXY_ENV_NAMES)


def _is_socks_proxy(value):
    return value.lower().startswith(_SOCKS_PROXY_PREFIXES)


def _socks_proxy_env_summary():
    return ", ".join(
        f"{name}={os.environ[name]}"
        for name in _PROXY_ENV_NAMES
        if _is_socks_proxy(os.environ.get(name, ""))
    )
