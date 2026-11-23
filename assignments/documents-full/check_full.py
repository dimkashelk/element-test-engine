"""Run structural and strict conformity checks for lesson 04, Documents."""
import importlib.util
from pathlib import Path

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location(
    'lesson_reference_checker', HERE.parent / 'catalogs-full/check_full.py')
CHECKER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECKER)


def main(argv=None):
    return CHECKER.main(argv, assignment_dir=HERE, title='Документы — полное соответствие')


if __name__ == '__main__':
    raise SystemExit(main())
