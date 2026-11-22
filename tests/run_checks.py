"""Run the repository's plain-function regression checks without installing pytest.

    .venv/bin/python tests/run_checks.py
    .venv/bin/python tests/run_checks.py test_webapp test_viewer_cli
"""
import argparse
import inspect
import importlib
from pathlib import Path
import sys
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('modules', nargs='*', help='test module names; defaults to all test_*.py')
    args = parser.parse_args()
    paths = [ROOT / 'tests' / (name.removesuffix('.py') + '.py') for name in args.modules] if args.modules else sorted((ROOT / 'tests').glob('test_*.py'))
    passed = failed = 0
    for path in paths:
        print(f'Running {path.stem}', flush=True)
        try:
            namespace = vars(importlib.import_module(path.stem))
        except Exception:
            traceback.print_exc()
            failed += 1
            continue
        for name, test in list(namespace.items()):
            if not name.startswith('test_') or not inspect.isfunction(test):
                continue
            try:
                test()
                passed += 1
            except Exception:
                print(f'FAIL {path.stem}.{name}', flush=True)
                traceback.print_exc()
                failed += 1
    print(f'{passed} passed, {failed} failed ({len(paths)} modules)', flush=True)
    return bool(failed)


if __name__ == '__main__':
    raise SystemExit(main())
