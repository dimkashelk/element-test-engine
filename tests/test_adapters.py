import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
import zipfile

from element_test.assignment import load_assignment
from element_test.indexer import index_module
from element_test.loader import open_project
from element_test.model import analyze
from element_test.runtime import extract_method, sbsl_literal
from element_test.types import parse_type
from element_test.yaml_io import InputError, load_yaml


class AdaptersTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def project(self):
        project = self.root / "student"
        project.mkdir()
        (project / "Проект.yaml").write_text("Имя: Пример\nРежимСовместимости: 9.10\n", encoding="utf-8")
        (project / "Заказ.yaml").write_text("ВидЭлемента: Документ\nИмя: Заказ\nРеквизиты:\n"
                                            "  - Имя: Цена\n    Тип: Число\n    ЗначениеПоУмолчанию: 1.5\n", encoding="utf-8")
        return project

    def test_analysis_preserves_sources_and_version(self):
        project = self.project()
        before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in project.iterdir()}
        with open_project(project) as root:
            model = analyze(root)
        self.assertEqual(model["compatibilityVersion"], "9.10")
        self.assertEqual(model["elements"][0]["properties"]["Реквизиты"][0]["ЗначениеПоУмолчанию"], 1.5)
        self.assertEqual(before, {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in project.iterdir()})
        self.assertEqual(model["sourceHash"], analyze(project)["sourceHash"])

    def test_empty_subsystem_is_valid_input(self):
        project = self.project()
        (project / "Подсистема.yaml").write_text("")
        self.assertEqual(analyze(project)["subsystems"][0]["properties"], {})

    def test_wrapped_archives_and_cleanup(self):
        for extension in ("zip", "tar", "tar.gz"):
            with self.subTest(extension=extension):
                archive = self.root / ("submission." + extension)
                content = "Имя: Пример\nРежимСовместимости: 9.0\n".encode()
                if extension == "zip":
                    with zipfile.ZipFile(archive, "w") as out:
                        out.writestr("Иванов/Проект.yaml", content)
                else:
                    with tarfile.open(archive, "w:gz" if extension.endswith("gz") else "w") as out:
                        entry = tarfile.TarInfo("Иванов/Проект.yaml")
                        entry.size = len(content)
                        out.addfile(entry, io.BytesIO(content))
                original = archive.read_bytes()
                with open_project(archive) as project:
                    self.assertEqual(analyze(project)["name"], "Пример")
                    extracted = project
                self.assertFalse(extracted.exists())
                self.assertEqual(archive.read_bytes(), original)

    def test_zip_traversal_duplicate_and_symlink(self):
        for variant in ("traversal", "duplicate", "symlink"):
            with self.subTest(variant=variant):
                archive = self.root / (variant + ".zip")
                with zipfile.ZipFile(archive, "w") as out:
                    if variant == "traversal":
                        out.writestr("../../escape", "malicious")
                    elif variant == "duplicate":
                        out.writestr("one", "first")
                        import warnings
                        with warnings.catch_warnings():
                            warnings.simplefilter("ignore")
                            out.writestr("one", "second")
                    else:
                        entry = zipfile.ZipInfo("link")
                        entry.create_system = 3
                        entry.external_attr = 0o120777 << 16
                        out.writestr(entry, "/etc/passwd")
                with self.assertRaises(InputError):
                    with open_project(archive):
                        pass

    def test_tar_link_and_ambiguous_roots(self):
        archive = self.root / "link.tar"
        with tarfile.open(archive, "w") as out:
            entry = tarfile.TarInfo("link")
            entry.type = tarfile.SYMTYPE
            entry.linkname = "/etc/passwd"
            out.addfile(entry)
        with self.assertRaises(InputError):
            with open_project(archive):
                pass
        first = self.project()
        second = self.root / "other"
        second.mkdir()
        (second / "Проект.yaml").write_text((first / "Проект.yaml").read_text())
        with self.assertRaisesRegex(InputError, "найдено: 2"):
            with open_project(self.root):
                pass

    def test_directory_symlink_and_yaml_cycles(self):
        project = self.project()
        (project / "link").symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(InputError):
            with open_project(project):
                pass
        yaml = self.root / "bad.yaml"
        for text in ("key: 1\nkey: 2", "x: &cycle [*cycle]"):
            yaml.write_text(text)
            with self.assertRaises(InputError):
                load_yaml(yaml)

    def test_union_grammar(self):
        self.assertEqual(parse_type("Б.Ссылка|А.Ссылка|?")[0], "А.Ссылка|Б.Ссылка|?")
        self.assertEqual(parse_type("Массив<Б.Ссылка|А.Ссылка>")[1], {"А", "Б"})
        for invalid in ("А|?", "А|Б?", "А?|Б", "Массив<А", "А||Б", "А|А", "Неопределено"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                parse_type(invalid)

    def test_missing_type_and_duplicate_member(self):
        project = self.project()
        (project / "Заказ.yaml").write_text("ВидЭлемента: Документ\nИмя: Заказ\nРеквизиты:\n"
                                            "  - Имя: Клиент\n    Тип: Нет.Ссылка?\n"
                                            "  - Имя: Клиент\n    Тип: Строка\n")
        codes = {d["code"] for d in analyze(project)["diagnostics"]}
        self.assertEqual(codes, {"duplicate_member", "unresolved_type"})

    def test_index_ignores_fake_methods(self):
        path = self.root / "Лица.Объект.xbsl"
        path.write_text('// метод Ложный()\nметод Текст(): Строка\n возврат "метод Фальшивый()"\n;\n'
                        '@Обработчик\nметод ПередЗаписью(Параметр: Массив<Строка>, Число: Число): Строка\n;\n')
        model = index_module(path, self.root)
        self.assertEqual([m["name"] for m in model["methods"]], ["Текст", "ПередЗаписью"])
        self.assertEqual(model["methods"][1]["annotations"], ["Обработчик"])
        self.assertEqual(len(model["methods"][1]["parameters"]), 2)

    def test_extract_does_not_rewrite_business_logic(self):
        source = '@Локально\nметод ФИО(Фамилия: Строка): Строка\n возврат "${Фамилия.Сократить()}"\n;\n'
        method, types = extract_method(source, "ФИО")
        self.assertIn('возврат "${Фамилия.Сократить()}"', method)
        self.assertNotIn("@Локально", method)
        self.assertEqual(types, ["Строка"])
        with self.assertRaises(InputError):
            extract_method('метод Цикл(): Строка\n пока Истина\n ;\n;\n', "Цикл")
        self.assertIn('\\${', sbsl_literal('${ОпасныйВызов()}', "Строка"))

    def test_assignment_rejects_bad_weights_and_duplicate_ids(self):
        assignment = self.root / "assignment.yaml"
        for body in ("checks: [{id: a, type: element, points: -1}]",
                     "checks: [{id: a, type: element}, {id: a, type: field}]"):
            assignment.write_text(body)
            with self.assertRaises(InputError):
                load_assignment(assignment)


if __name__ == "__main__":
    unittest.main()
