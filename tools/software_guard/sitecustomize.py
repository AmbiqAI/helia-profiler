"""Install the software-only guard during inherited Python child startup."""

import os

try:
    from guard import install

    install()
except BaseException:
    # Python otherwise prints sitecustomize errors and continues unguarded.
    os._exit(126)
