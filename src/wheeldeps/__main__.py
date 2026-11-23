"""Command line entry point: ``wheeldeps`` or ``python -m wheeldeps``."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import closure, current_environment, external_requirements, read_wheel


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="wheeldeps",
        description="List what a set of wheels needs and the set does not provide.",
    )
    parser.add_argument("wheels", nargs="+", type=Path, metavar="WHEEL")
    parser.add_argument("--json", action="store_true", help="print JSON")
    parser.add_argument(
        "--python-version",
        metavar="X.Y",
        help="evaluate markers as if running this Python version",
    )
    parser.add_argument("--platform", metavar="SYS_PLATFORM", help="e.g. linux, win32, darwin")
    parser.add_argument(
        "--closure",
        metavar="NAME",
        nargs="*",
        help="print the wheels in the set that NAME pulls in",
    )
    args = parser.parse_args(argv)

    missing_files = [str(path) for path in args.wheels if not path.is_file()]
    if missing_files:
        print(f"no such file: {', '.join(missing_files)}", file=sys.stderr)
        return 2

    environment = current_environment()
    if args.python_version:
        environment["python_version"] = args.python_version
        environment["python_full_version"] = f"{args.python_version}.0"
    if args.platform:
        environment["sys_platform"] = args.platform

    try:
        wheels = [read_wheel(path) for path in args.wheels]
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.closure is not None:
        names = closure(wheels, args.closure or None, environment=environment)
        if args.json:
            print(json.dumps(names, indent=2))
        else:
            for name in names:
                print(name)
        return 0

    requirements = external_requirements(wheels, environment=environment)
    if args.json:
        print(
            json.dumps(
                [
                    {
                        "name": requirement.name,
                        "specifier": requirement.specifier,
                        "marker": requirement.marker,
                        "provided": False,
                    }
                    for requirement in requirements
                ],
                indent=2,
            )
        )
        return 0
    if not requirements:
        print(f"{len(wheels)} wheel(s) need nothing from outside the set")
        return 0
    for requirement in requirements:
        print(requirement.raw)
    print(f"{len(requirements)} requirement(s) not provided by the set", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
