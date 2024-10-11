"""
The extractor module is responsible for extracting information from the given
URL. The module is designed to be modular, allowing for the addition of new
extractors without modifying the core codebase.

New extactors can be added by creating a new class that inherits from the
:class:`Outlet` class.
"""

from importlib.resources import files
from ..abc import Outlet

def get_default_extractors():
    """
    Buildin extractors.

    This function will return a list of all the extractors in the `extractor`
    package.
    """
    pkg_file_list = []
    for file in files(__package__).iterdir():
        if file.name[0] in ["_", "."]: continue

        if file.is_dir() and (file / "__init__.py").exists():
            pkg_file_list.append(f"{__package__}.{file.name}")
        elif file.suffix == ".py":
            pkg_file_list.append(f"{__package__}.{file.name[0:-3]}")

    # Get all the classes from the files
    extractors = []
    for file in pkg_file_list:
        mod = __import__(file, fromlist=[""])
        for name in dir(mod):
            obj = getattr(mod, name)

            if isinstance(obj, type) and issubclass(obj, Outlet):
                # Skip the base class and "hidden" classes
                if obj is Outlet: continue
                if obj.__name__[0] in ["_", "."]: continue

                extractors.append(obj()) 

    return extractors

def get_extractors():
    """
    Get all the extractors.

    ..todo:: Add support for custom extractors
    """
    default_extractors = get_default_extractors()
    # Sort the extractors by weight
    return sorted(default_extractors, key=lambda x: x.weight, reverse=True)
