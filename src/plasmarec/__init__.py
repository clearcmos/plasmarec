"""plasmarec: record one monitor on KDE Plasma, with a status card kept out of the video."""

__version__ = "0.1.0"

# The KWin ghost script (data/kwin/plasmarecghost) finds plasmarec's windows
# through the process that owns this bus name, and confirms each one on this
# path and interface. The two sides must agree; tests/test_kwin_script.py
# checks that they do.
APP_ID = "io.github.clearcmos.plasmarec"
OBJECT_PATH = "/io/github/clearcmos/plasmarec"
GHOST_INTERFACE = "io.github.clearcmos.plasmarec.Ghost"
KWIN_SCRIPT = "plasmarecghost"
