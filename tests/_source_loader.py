"""Load the real plugin modules without constructing or starting the Star."""
import importlib
import importlib.machinery
from pathlib import Path
import sys
from types import ModuleType

SOURCE = Path(__file__).resolve().parents[1]
PREFIX = "_disaster_public_regression"
package = ModuleType(PREFIX)
package.__path__ = [str(SOURCE)]
package.__package__ = PREFIX
package.__spec__ = importlib.machinery.ModuleSpec(PREFIX, loader=None, is_package=True)
sys.modules[PREFIX] = package
importlib.import_module(PREFIX + ".main")


def module(name):
    return importlib.import_module(PREFIX + "." + name)
