"""Tests for wheeldeps."""

import json
import zipfile

import pytest

from wheeldeps import (
    Requirement,
    Wheel,
    closure,
    evaluate_marker,
    external_requirements,
    normalize,
    parse_requirement,
    read_wheel,
    read_wheels,
)

ENV = {
    "python_version": "3.12",
    "python_full_version": "3.12.1",
    "sys_platform": "linux",
    "platform_system": "Linux",
    "os_name": "posix",
    "implementation_name": "cpython",
    "extra": "",
}


def make_wheel(tmp_path, name, version="1.0", requires=(), python=">=3.9"):
    metadata = ["Metadata-Version: 2.1", f"Name: {name}", f"Version: {version}"]
    if python:
        metadata.append(f"Requires-Python: {python}")
    metadata += [f"Requires-Dist: {requirement}" for requirement in requires]
    path = tmp_path / f"{name.replace('-', '_')}-{version}-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            f"{name.replace('-', '_')}-{version}.dist-info/METADATA",
            "\n".join(metadata) + "\n\nbody text\n",
        )
    return path


class TestNormalize:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Zope.Interface", "zope-interface"),
            ("Django_Rest", "django-rest"),
            ("a--b__c..d", "a-b-c-d"),
            ("  PyYAML  ", "pyyaml"),
        ],
    )
    def test_names(self, raw, expected):
        assert normalize(raw) == expected


class TestParseRequirement:
    def test_bare_name(self):
        requirement = parse_requirement("requests")
        assert (requirement.name, requirement.specifier, requirement.extras) == (
            "requests",
            "",
            (),
        )

    def test_with_a_specifier(self):
        assert parse_requirement("requests>=2.0,<3").specifier == ">=2.0,<3"

    def test_with_extras(self):
        requirement = parse_requirement("requests[socks,security]>=2")
        assert requirement.extras == ("socks", "security")
        assert requirement.name == "requests"

    def test_with_a_marker(self):
        requirement = parse_requirement('tomli; python_version < "3.11"')
        assert requirement.name == "tomli"
        assert requirement.marker == 'python_version < "3.11"'

    def test_the_raw_line_is_kept(self):
        assert parse_requirement("requests>=2").raw == "requests>=2"

    def test_canonical_name(self):
        assert parse_requirement("Zope.Interface").canonical == "zope-interface"

    def test_garbage_raises(self):
        with pytest.raises(ValueError, match="not a requirement"):
            parse_requirement("!!!")


class TestEvaluateMarker:
    def test_no_marker_is_true(self):
        assert evaluate_marker(None, ENV) is True

    def test_simple_comparison_true(self):
        assert evaluate_marker('python_version >= "3.9"', ENV) is True

    def test_simple_comparison_false(self):
        assert evaluate_marker('python_version < "3.9"', ENV) is False

    def test_version_comparison_is_numeric_not_lexical(self):
        # "3.12" > "3.9" as a string is false, which is the usual bug.
        assert evaluate_marker('python_version > "3.9"', ENV) is True

    def test_equality_of_a_string_variable(self):
        assert evaluate_marker('sys_platform == "linux"', ENV) is True

    def test_inequality(self):
        assert evaluate_marker('sys_platform != "win32"', ENV) is True

    def test_and(self):
        assert evaluate_marker('sys_platform == "linux" and python_version >= "3.9"', ENV) is True
        assert evaluate_marker('sys_platform == "linux" and python_version < "3.9"', ENV) is False

    def test_or(self):
        assert evaluate_marker('sys_platform == "win32" or sys_platform == "linux"', ENV) is True
        assert evaluate_marker('sys_platform == "win32" or sys_platform == "darwin"', ENV) is False

    def test_not(self):
        assert evaluate_marker('not sys_platform == "win32"', ENV) is True

    def test_parentheses(self):
        marker = '(sys_platform == "win32" or sys_platform == "linux") and python_version >= "3.9"'
        assert evaluate_marker(marker, ENV) is True

    def test_in(self):
        assert evaluate_marker('python_version in "3.11 3.12 3.13"', ENV) is True

    def test_not_in(self):
        assert evaluate_marker('python_version not in "3.8 3.9"', ENV) is True

    def test_unknown_variable_is_false(self):
        assert evaluate_marker('nonsense == "x"', ENV) is False

    def test_extra_is_empty_by_default(self):
        assert evaluate_marker('extra == "socks"', ENV) is False

    def test_extra_can_be_set(self):
        assert evaluate_marker('extra == "socks"', {**ENV, "extra": "socks"}) is True

    def test_whitespace_is_irrelevant(self):
        assert evaluate_marker('  python_version>="3.9"  ', ENV) is True

    def test_garbage_raises(self):
        with pytest.raises(ValueError):
            evaluate_marker("python_version >=", ENV)


class TestReadWheel:
    def test_reads_name_and_version(self, tmp_path):
        wheel = read_wheel(make_wheel(tmp_path, "demo", "2.1"))
        assert (wheel.name, wheel.version) == ("demo", "2.1")

    def test_reads_requires_python(self, tmp_path):
        assert read_wheel(make_wheel(tmp_path, "demo", python=">=3.10")).requires_python == ">=3.10"

    def test_reads_requirements(self, tmp_path):
        wheel = read_wheel(make_wheel(tmp_path, "demo", requires=["requests>=2", "tomli"]))
        assert [requirement.name for requirement in wheel.requirements] == ["requests", "tomli"]

    def test_body_text_is_not_parsed_as_a_header(self, tmp_path):
        wheel = read_wheel(make_wheel(tmp_path, "demo", requires=["requests"]))
        assert len(wheel.requirements) == 1

    def test_canonical_name(self, tmp_path):
        assert read_wheel(make_wheel(tmp_path, "Demo_Pkg")).canonical == "demo-pkg"

    def test_a_zip_without_metadata_raises(self, tmp_path):
        path = tmp_path / "fake-1.0-py3-none-any.whl"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("something.txt", "hi")
        with pytest.raises(ValueError, match="no .dist-info/METADATA"):
            read_wheel(path)

    def test_read_wheels(self, tmp_path):
        paths = [make_wheel(tmp_path, "a"), make_wheel(tmp_path, "b")]
        assert [wheel.name for wheel in read_wheels(paths)] == ["a", "b"]


class TestExternalRequirements:
    def test_a_dependency_is_missing_when_not_in_the_set(self, tmp_path):
        wheels = read_wheels([make_wheel(tmp_path, "app", requires=["requests>=2"])])
        assert [
            requirement.name for requirement in external_requirements(wheels, environment=ENV)
        ] == ["requests"]

    def test_a_provided_dependency_is_not_missing(self, tmp_path):
        wheels = read_wheels(
            [
                make_wheel(tmp_path, "app", requires=["Requests>=2"]),
                make_wheel(tmp_path, "requests", "2.31"),
            ]
        )
        assert external_requirements(wheels, environment=ENV) == []

    def test_markers_filter_requirements(self, tmp_path):
        wheels = read_wheels(
            [make_wheel(tmp_path, "app", requires=['tomli; python_version < "3.11"'])]
        )
        assert external_requirements(wheels, environment=ENV) == []

    def test_a_marker_that_holds_keeps_the_requirement(self, tmp_path):
        wheels = read_wheels(
            [make_wheel(tmp_path, "app", requires=['tomli; python_version >= "3.9"'])]
        )
        assert [
            requirement.name for requirement in external_requirements(wheels, environment=ENV)
        ] == ["tomli"]

    def test_duplicates_are_collapsed(self, tmp_path):
        wheels = read_wheels(
            [
                make_wheel(tmp_path, "a", requires=["requests"]),
                make_wheel(tmp_path, "b", requires=["Requests>=2"]),
            ]
        )
        assert len(external_requirements(wheels, environment=ENV)) == 1

    def test_results_are_sorted(self, tmp_path):
        wheels = read_wheels([make_wheel(tmp_path, "app", requires=["zzz", "aaa", "mmm"])])
        assert [
            requirement.canonical for requirement in external_requirements(wheels, environment=ENV)
        ] == ["aaa", "mmm", "zzz"]

    def test_a_wheel_without_requirements(self, tmp_path):
        wheels = read_wheels([make_wheel(tmp_path, "solo")])
        assert external_requirements(wheels, environment=ENV) == []


class TestClosure:
    def test_closure_follows_the_graph(self, tmp_path):
        wheels = read_wheels(
            [
                make_wheel(tmp_path, "app", requires=["lib"]),
                make_wheel(tmp_path, "lib", requires=["core"]),
                make_wheel(tmp_path, "core"),
                make_wheel(tmp_path, "unrelated"),
            ]
        )
        assert closure(wheels, ["app"], environment=ENV) == ["app", "core", "lib"]

    def test_closure_of_everything(self, tmp_path):
        wheels = read_wheels([make_wheel(tmp_path, "a"), make_wheel(tmp_path, "b")])
        assert closure(wheels, environment=ENV) == ["a", "b"]

    def test_an_unknown_root_is_ignored(self, tmp_path):
        wheels = read_wheels([make_wheel(tmp_path, "a")])
        assert closure(wheels, ["nope"], environment=ENV) == []

    def test_markers_are_applied_when_following(self, tmp_path):
        wheels = read_wheels(
            [
                make_wheel(tmp_path, "app", requires=['winonly; sys_platform == "win32"']),
                make_wheel(tmp_path, "winonly"),
            ]
        )
        assert closure(wheels, ["app"], environment=ENV) == ["app"]


class TestWheelAndRequirementTypes:
    def test_wheel_canonical_name(self):
        assert Wheel("Demo_Pkg", "1").canonical == "demo-pkg"

    def test_requirement_str_prefers_the_raw_line(self):
        assert str(Requirement("a", raw="a>=1")) == "a>=1"

    def test_requirement_str_falls_back_to_the_name(self):
        assert str(Requirement("a")) == "a"


class TestCommandLine:
    def run(self, argv):
        from wheeldeps.__main__ import main

        return main(argv)

    def test_lists_missing_requirements(self, tmp_path, capsys):
        path = make_wheel(tmp_path, "app", requires=["requests>=2"])
        assert self.run([str(path)]) == 0
        assert "requests>=2" in capsys.readouterr().out

    def test_quiet_when_nothing_is_missing(self, tmp_path, capsys):
        path = make_wheel(tmp_path, "app")
        assert self.run([str(path)]) == 0
        assert "need nothing from outside" in capsys.readouterr().out

    def test_json_output(self, tmp_path, capsys):
        path = make_wheel(tmp_path, "app", requires=["requests>=2"])
        assert self.run([str(path), "--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload[0]["name"] == "requests"
        assert payload[0]["specifier"] == ">=2"

    def test_python_version_override(self, tmp_path, capsys):
        path = make_wheel(tmp_path, "app", requires=['tomli; python_version < "3.11"'])
        assert self.run([str(path), "--python-version", "3.10"]) == 0
        assert "tomli" in capsys.readouterr().out

    def test_platform_override(self, tmp_path, capsys):
        path = make_wheel(tmp_path, "app", requires=['pywin32; sys_platform == "win32"'])
        assert self.run([str(path), "--platform", "win32"]) == 0
        assert "pywin32" in capsys.readouterr().out

    def test_closure_output(self, tmp_path, capsys):
        paths = [
            make_wheel(tmp_path, "app", requires=["lib"]),
            make_wheel(tmp_path, "lib"),
        ]
        assert self.run([*(str(path) for path in paths), "--closure", "app"]) == 0
        assert capsys.readouterr().out.split() == ["app", "lib"]

    def test_missing_file_exits_two(self, tmp_path, capsys):
        assert self.run([str(tmp_path / "nope.whl")]) == 2
        assert "no such file" in capsys.readouterr().err

    def test_a_non_wheel_exits_two(self, tmp_path, capsys):
        path = tmp_path / "x.whl"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("a.txt", "x")
        assert self.run([str(path)]) == 2
        assert "no .dist-info/METADATA" in capsys.readouterr().err


class TestRigour:
    """Edge cases that the first pass missed: malformed metadata and marker grammar."""

    def test_a_leading_blank_line_is_skipped(self, tmp_path):
        path = tmp_path / "x-1.0-py3-none-any.whl"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr(
                "x-1.0.dist-info/METADATA",
                "\nName: x\nVersion: 1.0\nRequires-Dist: sqlalchemy\n\nbody\n",
            )
        wheel = read_wheel(path)
        assert wheel.name == "x"
        assert [requirement.name for requirement in wheel.requirements] == ["sqlalchemy"]

    def test_metadata_without_a_name_is_rejected(self, tmp_path):
        path = tmp_path / "x-1.0-py3-none-any.whl"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("x-1.0.dist-info/METADATA", "Version: 1.0\n\nbody\n")
        with pytest.raises(ValueError, match="has no Name"):
            read_wheel(path)

    def test_a_trailing_plus_and_empty_chunks_in_a_version(self):
        assert evaluate_marker('python_version > "3.9+"', ENV)
        assert evaluate_marker('python_version > "3.12.1"', ENV) is False

    def test_a_non_numeric_version_chunk(self):
        # "rc" sorts after the bare release, so 3.12.0rc1 > 3.12.0
        assert evaluate_marker('python_version > "3.12.0"', {**ENV, "python_version": "3.12.0rc1"})

    def test_compatible_release_operator(self):
        assert evaluate_marker('python_version ~= "3.12"', ENV)
        assert evaluate_marker('python_version ~= "3.11"', ENV) is False

    def test_less_or_equal(self):
        assert evaluate_marker('python_version <= "3.12"', ENV)
        assert evaluate_marker('python_version <= "3.11"', ENV) is False

    def test_an_unknown_operator(self):
        from wheeldeps import _compare

        with pytest.raises(ValueError, match="unknown operator"):
            _compare("1", "~~", "1")

    @pytest.mark.parametrize(
        ("marker", "message"),
        [
            ("python_version ! 3.9", "cannot parse marker"),
            ('python_version == "3.9" and ', "marker ended early"),
            ('python_version == "3.9" extra', "unexpected"),
            ('"3.9" == "3.9"', "expected a marker variable"),
            ('python_version "3.9"', "expected an operator"),
            ("python_version == sys_platform", "expected a quoted value"),
        ],
    )
    def test_malformed_markers_raise(self, marker, message):
        with pytest.raises(ValueError, match=message):
            evaluate_marker(marker, ENV)

    def test_a_parenthesis_that_closes_wrongly(self):
        with pytest.raises(ValueError, match="expected '\)'"):
            evaluate_marker('(python_version == "3.9" "3.9")', ENV)

    def test_an_unknown_variable_is_false(self):
        assert evaluate_marker('java_version == "17"', ENV) is False

    def test_the_module_entry_point_prints_usage(self):
        import subprocess
        import sys

        result = subprocess.run(
            [sys.executable, "-m", "wheeldeps", "--help"], capture_output=True, text=True
        )
        assert result.returncode == 0
        assert "wheel" in result.stdout.lower()


class TestJsonOutput:
    def test_json_names(self, tmp_path, capsys):
        from wheeldeps.__main__ import main

        path = make_wheel(tmp_path, "app", requires=["requests"])
        assert main([str(path), "--closure", "--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload == [payload[0]]
        assert "app" in payload


class TestRigour2:
    """Version chunks that are pure numbers."""

    def test_a_bare_major_version_has_one_chunk(self):
        from wheeldeps import _version_key

        assert _version_key("1") == ((0, 1, ""),)

    def test_a_chunk_that_starts_with_a_letter(self):
        from wheeldeps import _version_key

        assert _version_key("rc1") == ((1, 0, "rc1"),)
        assert _version_key("1.rc1") == ((0, 1, ""), (1, 0, "rc1"))
