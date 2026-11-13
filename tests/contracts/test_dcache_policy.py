"""Execute the emitted cache setup against status-reporting HAL doubles."""

import os
import shutil
import subprocess

import pytest

from helia_profiler.firmware.render import _jinja_env


@pytest.fixture(scope="module")
def cache_program(tmp_path_factory):
    compiler = shutil.which("c++")
    if compiler is None:
        pytest.skip("host C++ compiler unavailable")
    root = tmp_path_factory.mktemp("cache-policy")
    source = root / "policy.cc"
    binary = root / ("policy.exe" if os.name == "nt" else "policy")
    body = _jinja_env.get_template("_dcache_policy.j2").render(dcache="off", has_dcache=True)
    source.write_text(
        "#include <stdint.h>\n#include <stdio.h>\n#include <stdlib.h>\n"
        "#define NSX_CACHE_HAS_EXPLICIT_DCACHE 1\n#define hpx_printf printf\n"
        "static uint32_t publish_status, disable_status;\n"
        'uint32_t nsx_cache_publish_writes() { puts("publish"); return publish_status; }\n'
        'uint32_t am_hal_cachectrl_dcache_disable() { puts("disable"); return disable_status; }\n'
        "int main(int argc, char **argv) {\n"
        "if (argc != 3) return 2;\n"
        "publish_status = (uint32_t)atoi(argv[1]); disable_status = (uint32_t)atoi(argv[2]);\n"
        + body
        + "\nreturn 0;\n}\n"
    )
    subprocess.run(
        [compiler, str(source), "-o", str(binary)], check=True, capture_output=True, timeout=30
    )
    return binary


@pytest.mark.parametrize(
    "publish,disable,expected,code",
    [
        (0, 0, ["publish", "disable", "HPX_DCACHE=off"], 0),
        (5, 0, ["publish", "HPX_ERROR=dcache_config status=5"], 1),
        (0, 7, ["publish", "disable", "HPX_ERROR=dcache_config status=7"], 1),
    ],
)
def test_failed_setup_cannot_announce_cache_off(cache_program, publish, disable, expected, code):
    result = subprocess.run(
        [str(cache_program), str(publish), str(disable)], capture_output=True, text=True, timeout=10
    )
    assert result.returncode == code
    assert result.stdout.splitlines() == expected
