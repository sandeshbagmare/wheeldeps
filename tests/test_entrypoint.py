"""The package is runnable: ``python -m wheeldeps`` and the module guard both work."""

import runpy
import subprocess
import sys
import warnings

import pytest


def test_the_module_entry_point_prints_usage():
    result = subprocess.run(
        [sys.executable, "-m", "wheeldeps", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "usage" in result.stdout.lower()


def test_the_main_guard_exits_with_the_status(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["wheeldeps", "--help"])
    # doctest collection imports this module, so runpy only warns that it is loaded.
    with warnings.catch_warnings(), pytest.raises(SystemExit) as caught:
        warnings.simplefilter("ignore", RuntimeWarning)
        runpy.run_module("wheeldeps", run_name="__main__")
    assert caught.value.code == 0
    assert "usage" in capsys.readouterr().out.lower()
