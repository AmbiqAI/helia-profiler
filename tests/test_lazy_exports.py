"""Cold root imports preserve concrete types and lazy resolution behavior."""

import subprocess
import sys
from typing import TYPE_CHECKING, assert_type

if TYPE_CHECKING:
    import helia_profiler as hpx
    from helia_profiler.config import ProfileConfig
    from helia_profiler.results import ProfileResult
    from helia_profiler.session import Session

    def _root_types(config: ProfileConfig) -> None:
        assert_type(hpx.Session(), Session)
        assert_type(hpx.ProfileConfig(model=config.model), ProfileConfig)
        assert_type(hpx.profile(config), ProfileResult)


def test_root_metadata_and_representative_exports_in_fresh_interpreter():
    probe = """
import sys
from unittest.mock import patch
import helia_profiler as hpx
assert not any(name.startswith('helia_profiler.') for name in sys.modules)
assert set(hpx.__all__) == set(hpx.__api_stability__)
assert set(hpx.__all__) <= set(dir(hpx))
assert dir(hpx) == sorted(set(vars(hpx)) | set(hpx._EXPORTS))
assert not any(name.startswith('helia_profiler.') for name in sys.modules)
original = hpx.import_module
with patch.object(hpx, 'import_module', wraps=original) as importer:
    cls = hpx.Session
    function = hpx.profile
    module = hpx.examples
    assert importer.call_count == 3
    assert hpx.Session is cls and hpx.profile is function and hpx.examples is module
    assert importer.call_count == 3
    try:
        hpx.not_an_export
    except AttributeError as exc:
        assert str(exc) == "module 'helia_profiler' has no attribute 'not_an_export'"
    else:
        raise AssertionError('unknown export accepted')
    assert importer.call_count == 3
assert cls is sys.modules['helia_profiler.session'].Session
assert function is sys.modules['helia_profiler.api'].profile
assert module is sys.modules['helia_profiler.examples']
assert 'JLinkProbe' not in vars(hpx)
failure = ImportError('target dependency missing')
with patch.object(hpx, 'import_module', side_effect=failure):
    try:
        hpx.JLinkProbe
    except ImportError as exc:
        assert exc is failure
    else:
        raise AssertionError('import failure swallowed')
assert 'JLinkProbe' not in vars(hpx)
"""
    subprocess.run([sys.executable, "-c", probe], check=True, capture_output=True, text=True)
