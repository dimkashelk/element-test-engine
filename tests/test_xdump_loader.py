"""Exercise xdump conversion through the same loader and CLI as source archives."""
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from element_test.loader import open_project
from element_test.model import analyze
from element_test.xdump import extract_project, write_tar
from element_test.yaml_io import InputError


REPO = Path(__file__).resolve().parent.parent
SOURCES = {
    "src/V/Пример/Проект.yaml": "Имя: Пример\nРежимСовместимости: 9.0\n",
    "src/V/Пример/Заказ.yaml": "ВидЭлемента: Документ\nИмя: Заказ\n",
    "src/V/Пример/Модуль.xbsl": "метод Ответ(): Число\n возврат 42\n;\n",
    "src/V/Пример/Пустой.xbsl": "",
    "src/V/Пример/.asm/Assembly.yaml": "internal",
    "data/records": "ignored",
}


def make_dump(directory, sources=None, name="submission.xdump"):
    application = io.BytesIO()
    with zipfile.ZipFile(application, "w") as archive:
        for path, content in (SOURCES if sources is None else sources).items():
            archive.writestr(path, content)
    dump = directory / name
    with zipfile.ZipFile(dump, "w") as archive:
        archive.writestr("application.zip", application.getvalue())
        archive.writestr("user-list.zip", b"ignored")
    return dump


class XdumpLoaderTest(unittest.TestCase):
    def test_conversion_matches_tar_and_preserves_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            dump = make_dump(base, name="submission.XDUMP")
            projects = extract_project(dump, base / "export")
            archive = write_tar(dump, projects)
            before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in (dump, archive)}
            with open_project(archive) as root:
                expected = analyze(root)
            with open_project(dump) as root:
                self.assertEqual(analyze(root), expected)
                self.assertEqual((root / "Пустой.xbsl").read_bytes(), b"")
                self.assertFalse((root / ".asm").exists())
                temporary = root.parents[1]
                self.assertTrue((temporary / "project.tar").is_file())
            self.assertFalse(temporary.exists())
            self.assertEqual(before, {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in before})

    def test_cleanup_when_check_fails_and_no_adjacent_tar(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            dump = make_dump(base)
            with self.assertRaisesRegex(RuntimeError, "check failed"):
                with open_project(dump) as root:
                    temporary = root.parents[1]
                    raise RuntimeError("check failed")
            self.assertFalse(temporary.exists())
            self.assertEqual(list(base.iterdir()), [dump])

    def test_invalid_dumps_are_input_errors(self):
        variants = {
            "missing_project": {"data/records": "ignored"},
            "traversal": {**SOURCES, "src/V/Пример/../../escape": "bad"},
            "multiple_projects": {**SOURCES, "src/V/Другой/Проект.yaml": "Имя: Другой\n"},
        }
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            for name, sources in variants.items():
                with self.subTest(name=name):
                    dump = make_dump(base, sources, name + ".xdump")
                    with self.assertRaises(InputError):
                        with open_project(dump):
                            pass
                    self.assertFalse(dump.with_suffix(".tar").exists())
            bad = base / "bad.xdump"
            for content in (b"not a zip",):
                bad.write_bytes(content)
                with self.assertRaisesRegex(InputError, "конвертации xdump"):
                    with open_project(bad):
                        pass
            with zipfile.ZipFile(bad, "w") as archive:
                archive.writestr("Проект.yaml", "Имя: Пример\n")
            with self.assertRaisesRegex(InputError, "application.zip"):
                with open_project(bad):
                    pass

    def test_source_limits_apply_before_conversion(self):
        with tempfile.TemporaryDirectory() as directory:
            dump = make_dump(Path(directory))
            with patch("element_test.xdump.MAX_SOURCE_BYTES", 1):
                with self.assertRaisesRegex(InputError, "лимит исходников"):
                    with open_project(dump):
                        pass

    @unittest.skipUnless((REPO / "script_u_10.0.2_1/lib").is_dir(),
                         "Требуется локальный Script executor")
    def test_all_cli_commands_accept_xdump(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            dump = make_dump(base)
            assignment = base / "assignment.yaml"
            assignment.write_text("checks:\n  - id: order\n    type: element\n    name: Заказ\n    points: 1\n",
                                  encoding="utf-8")
            before = dump.read_bytes()
            for command in ("inspect", "validate", "test", "run"):
                with self.subTest(command=command):
                    output = base / command
                    args = ([command, str(dump), "--output", str(output)]
                            if command in ("inspect", "validate") else
                            [command, "--project", str(dump), "--assignment", str(assignment),
                             "--output", str(output)])
                    result = subprocess.run([sys.executable, "-m", "element_test.bridge", *args],
                                            cwd=REPO, capture_output=True, text=True, timeout=60)
                    self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
                    data = json.loads((output / "result.json" if command in ("test", "run")
                                       else output).read_text(encoding="utf-8"))
                    if command in ("test", "run"):
                        self.assertEqual(data["checks"][0]["status"], "PASS")
                        self.assertTrue((output / "report.html").is_file())
                    elif command == "validate":
                        self.assertTrue(data["valid"])
            self.assertEqual(dump.read_bytes(), before)
            self.assertFalse(dump.with_suffix(".tar").exists())


if __name__ == "__main__":
    unittest.main()
