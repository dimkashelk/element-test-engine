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
from element_test.batch import run_batch
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
    def test_service_yaml_errors_keep_source_path_and_line(self):
        with tempfile.TemporaryDirectory() as directory:
            sources = dict(SOURCES)
            sources["src/V/Пример/Локализация/En/ЛокализованныеСтроки.yaml"] = (
                """Строки: {Заголовок: "bad \\' escape"}\n""")
            dump = make_dump(Path(directory), sources)
            with open_project(dump) as root, self.assertRaisesRegex(
                    InputError, r"(?s)Локализация/En/ЛокализованныеСтроки.yaml:.*line 1"):
                analyze(root)

    @unittest.skipUnless((REPO / "script_u_10.0.2_1/lib").is_dir(),
                         "Требуется локальный Script executor")
    def test_batch_cache_distinguishes_library_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            sources = {
                "src/author/App/Проект.yaml":
                    "Имя: App\nПоставщик: author\nРежимСовместимости: 9.0\n"
                    "Библиотеки:\n  - {Поставщик: vendor, Имя: Lib, Версия: 1.0}\n",
                "src/author/App/Thing.yaml": "ВидЭлемента: Документ\nИмя: Thing\n",
                "src/vendor/Lib/Проект.yaml":
                    "Имя: Lib\nПоставщик: vendor\nВерсия: 1.0\n"
                    "ВидПроекта: Библиотека\nРежимСовместимости: 9.0\n",
                "src/vendor/Lib/Ресурсы/icon.svg": "first",
            }
            first = make_dump(base, sources, "first.xdump")
            copy = base / "copy.xdump"
            copy.write_bytes(first.read_bytes())
            changed = dict(sources)
            changed["src/vendor/Lib/Ресурсы/icon.svg"] = "second"
            second = make_dump(base, changed, "second.xdump")
            assignment = base / "assignment.yaml"
            assignment.write_text("name: P0\nchecks:\n  - id: thing\n    type: element\n"
                                  "    name: Thing\n    points: 1\n", encoding="utf-8")
            manifest = base / "manifest.json"
            manifest.write_text(json.dumps({
                "schemaVersion": "1.0", "assignment": "assignment.yaml", "assignmentId": "p0",
                "submissions": [{"studentId": f"s-{i}", "project": path.name,
                                 "projectName": "author::App"}
                                for i, path in enumerate((first, copy, second), 1)]
            }), encoding="utf-8")
            with patch.dict("os.environ", {"ELEMENT_TEST_CACHE_DIR": str(base / "cache")}):
                self.assertEqual(run_batch(manifest, base / "output", 1, False), 0)
            index = json.loads((base / "output/batch-result.json").read_text())
            self.assertEqual([item["cacheHit"] for item in index["submissions"]], [False, True, False])
            grade = json.loads((base / "output/submissions/000003/grading.json").read_text())
            self.assertEqual(grade["projectIdentity"]["Поставщик"], "author")
            self.assertEqual(grade["libraries"][0]["name"], "Lib")
            html = (base / "output/submissions/000003/report.html").read_text()
            self.assertIn("author::App", html)
            self.assertIn("vendor::Lib", html)

    def test_application_selects_exact_library_and_hashes_its_contents(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            sources = {
                "src/author/App/Проект.yaml":
                    "Имя: App\nПоставщик: author\nРежимСовместимости: 9.0\n"
                    "Библиотеки:\n  - {Поставщик: vendor, Имя: Lib, Версия: 1.1.1}\n",
                "src/author/App/Ресурсы/Ресурсы.yaml": "ОбластьВидимости: ВПроекте\n",
                "src/author/App/Локализация/En/ЛокализованныеСтроки.yaml":
                    "Строки: {Привет: Hello}\n",
                "src/author/App/Фрагмент.yaml":
                    "ВидЭлемента: ФрагментКомандногоИнтерфейса\nИмя: Фрагмент\n"
                    "Элементы: [=Lib.Открыть, {Тип: НавигационнаяКоманда}]\n",
                "src/vendor/Lib/Проект.yaml":
                    "Имя: Lib\nПоставщик: vendor\nВерсия: 1.1.1\n"
                    "ВидПроекта: Библиотека\nРежимСовместимости: 9.0\n",
                "src/vendor/Lib/Module.xbsl": "метод Ответ(): Число\n возврат 1\n;\n",
                "src/vendor/Lib/Ресурсы/logo.svg": "<svg/>",
            }
            first = make_dump(base, sources, "first.xdump")
            with open_project(first) as root:
                model = analyze(root)
                self.assertEqual(root.name, "App")
                self.assertEqual(len(model["serviceFiles"]), 2)
                self.assertEqual(model["projectIdentity"]["Поставщик"], "author")
                self.assertEqual([(x["provider"], x["name"], x["version"])
                                  for x in model["libraries"]], [("vendor", "Lib", "1.1.1")])
                self.assertEqual(len(model["libraries"][0]["model"]["modules"]), 1)
            with open_project(first, "vendor::Lib") as root:
                self.assertEqual(root.name, "Lib")
                self.assertEqual(analyze(root)["libraries"], [])
            changed = dict(sources)
            changed["src/vendor/Lib/Module.xbsl"] = sources["src/vendor/Lib/Module.xbsl"].replace("1", "2")
            second = make_dump(base, changed, "second.xdump")
            with open_project(second) as root:
                self.assertNotEqual(analyze(root)["sourceHash"], model["sourceHash"])
            changed_asset = dict(sources)
            changed_asset["src/vendor/Lib/Ресурсы/logo.svg"] = "<svg><rect/></svg>"
            asset_dump = make_dump(base, changed_asset, "changed-asset.xdump")
            with open_project(asset_dump) as root:
                self.assertNotEqual(analyze(root)["sourceHash"], model["sourceHash"])
            alternate = dict(sources)
            alternate["src/other/Lib/Проект.yaml"] = sources["src/vendor/Lib/Проект.yaml"].replace(
                "Поставщик: vendor", "Поставщик: other")
            alternate["src/other/Lib/Module.xbsl"] = "метод Ответ(): Число\n возврат 9\n;\n"
            duplicate_names = make_dump(base, alternate, "duplicate-names.xdump")
            with open_project(duplicate_names) as root:
                self.assertEqual(analyze(root)["libraries"][0]["provider"], "vendor")
            with open_project(duplicate_names, "other::Lib") as root:
                self.assertEqual(analyze(root)["projectIdentity"]["Поставщик"], "other")
            with self.assertRaisesRegex(InputError, "неоднозначен"):
                with open_project(duplicate_names, "Lib"):
                    pass
            ambiguous = dict(alternate)
            ambiguous["src/other/Lib/Проект.yaml"] = sources["src/vendor/Lib/Проект.yaml"]
            ambiguous_dump = make_dump(base, ambiguous, "ambiguous-library.xdump")
            with open_project(ambiguous_dump) as root, self.assertRaisesRegex(InputError, "неоднозначна"):
                analyze(root)
            wrong_kind = dict(sources)
            wrong_kind["src/vendor/Lib/Проект.yaml"] = sources["src/vendor/Lib/Проект.yaml"].replace(
                "ВидПроекта: Библиотека\n", "")
            wrong_kind_dump = make_dump(base, wrong_kind, "wrong-kind.xdump")
            with open_project(wrong_kind_dump, "author::App") as root, self.assertRaisesRegex(
                    InputError, "не совпадает по версии или виду"):
                analyze(root)
            transitive = dict(sources)
            transitive["src/vendor/Lib/Проект.yaml"] += (
                "Библиотеки:\n  - {Поставщик: vendor, Имя: Base, Версия: 2.0}\n")
            transitive["src/vendor/Base/Проект.yaml"] = (
                "Имя: Base\nПоставщик: vendor\nВерсия: 2.0\n"
                "ВидПроекта: Библиотека\nРежимСовместимости: 9.0\n")
            transitive_dump = make_dump(base, transitive, "transitive.xdump")
            with open_project(transitive_dump) as root:
                self.assertEqual([x["name"] for x in analyze(root)["libraries"]], ["Lib", "Base"])
            transitive["src/vendor/Base/Проект.yaml"] += (
                "Библиотеки:\n  - {Поставщик: vendor, Имя: Lib, Версия: 1.1.1}\n")
            cycle_dump = make_dump(base, transitive, "cycle.xdump")
            with open_project(cycle_dump) as root, self.assertRaisesRegex(InputError, "Циклическая зависимость"):
                analyze(root)
            for value, problem in (("1.1.2", "не совпадает"), ("", "не найдена")):
                broken = dict(sources)
                if value:
                    broken["src/vendor/Lib/Проект.yaml"] = sources["src/vendor/Lib/Проект.yaml"].replace(
                        "Версия: 1.1.1", f"Версия: {value}")
                else:
                    del broken["src/vendor/Lib/Проект.yaml"]
                dump = make_dump(base, broken, f"broken-{value or 'empty'}.xdump")
                with open_project(dump) as root, self.assertRaisesRegex(InputError, problem):
                    analyze(root)

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
                        grading = json.loads((output / "grading.json").read_text(encoding="utf-8"))
                        self.assertEqual((grading["schemaVersion"], grading["status"]), ("1.0", "passed"))
                        self.assertEqual(grading["feedback"][0]["criterionId"], "order")
                        self.assertTrue((output / "report.html").is_file())
                    elif command == "validate":
                        self.assertTrue(data["valid"])
            self.assertEqual(dump.read_bytes(), before)
            self.assertFalse(dump.with_suffix(".tar").exists())


if __name__ == "__main__":
    unittest.main()
