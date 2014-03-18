#!/usr/bin/env python
import os
import sys
import warnings

# We are overriding a few packages (like Django) from the system path.
#   Suppress those warnings
warnings.filterwarnings('ignore', message=r'Module .*? is being added to sys\.path', append=True)

# Now build the paths that point to all of the project pieces
PROJECT_PATH = os.path.dirname(os.path.realpath(__file__))
PROJECT_PYTHON_PATHS = [
    os.path.join(PROJECT_PATH, "..", "ka-lite", "python-packages"),
    PROJECT_PATH,
    os.path.join(PROJECT_PATH, ".."),
    os.path.join(PROJECT_PATH, "..", 'ka-lite'),
    os.path.join(PROJECT_PATH, "..", 'ka-lite', 'kalite'),
]
sys.path = PROJECT_PYTHON_PATHS + sys.path

# Now we can get started.

from django.core.management import execute_manager
import settings
from settings import LOG as logging


########################
# kaserve
########################

if settings.DEBUG:
    # In debug mode, add useful debugging flags
    for flag in ["traceback"]:
        dashed_flag = "--%s" % flag
        if dashed_flag not in sys.argv:
            sys.argv.append(dashed_flag)

########################
# clean_pyc
########################

if len(sys.argv) == 2 and sys.argv[1] == "clean_pyc":
    sys.argv += ["--path", ".."]

########################
# Static files
########################

if "runserver" in sys.argv and "--nostatic" not in sys.argv:
    sys.argv += ["--nostatic"]


if __name__ == "__main__":
    execute_manager(settings)
