"""Check nested archive extraction, source fidelity, and unsafe inputs."""
import io
from pathlib import Path
import tarfile
import tempfile
import unicodedata
import unittest
import zipfile

from extract_xdump import extract_project, write_tar


REPO = Path(__file__).resolve().parent.parent


class ExtractXdumpTest(unittest.TestCase):
    def make_dump(self, directory, files):
        application = io.BytesIO()
        with zipfile.ZipFile(application, "w") as archive:
            for name, content in files.items():
                archive.writestr(name, content)
        dump = directory / "project.xdump"
        with zipfile.ZipFile(dump, "w") as archive:
            archive.writestr("application.zip", application.getvalue())
            archive.writestr("user-list.zip", b"ignored")
        return dump

    def test_sources_preserved_and_service_files_excluded(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            prefix = "src/Поставщик/Проект/"
            dump = self.make_dump(base, {
                prefix + "Проект.yaml": "Имя: Проект\n",
                prefix + "CRM/Настройки.xbsl": b"",
                prefix + ".asm/Assembly.yaml": b"internal",
                "data/records": b"data",
            })
            output = base / "output"
            self.assertEqual(extract_project(dump, output), [output / "Проект"])
            self.assertEqual(sorted(str(p.relative_to(output)) for p in output.rglob("*")
                                    if p.is_file()),
                             ["Проект/CRM/Настройки.xbsl", "Проект/Проект.yaml"])
            self.assertEqual((output / "Проект/CRM/Настройки.xbsl").read_bytes(), b"")
            with self.assertRaisesRegex(ValueError, "уже существует"):
                extract_project(dump, output)

    def test_path_traversal_rejected_before_output_created(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            dump = self.make_dump(base, {"src/P/P/Проект.yaml": b"",
                                         "src/P/P/../../escape": b"bad"})
            with self.assertRaisesRegex(ValueError, "Небезопасный путь"):
                extract_project(dump, base / "output")
            self.assertFalse((base / "output").exists())

    def test_missing_project_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            dump = self.make_dump(base, {"data/records": b""})
            with self.assertRaisesRegex(ValueError, "не найден Проект.yaml"):
                extract_project(dump, base / "output")

    def test_tar_next_to_dump_preserves_project_root_and_existing_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            dump = self.make_dump(base, {"src/V/P/Проект.yaml": b"project",
                                         "src/V/P/CRM/Module.xbsl": b""})
            projects = extract_project(dump, base / "output")
            archive_path = write_tar(dump, projects)
            self.assertEqual(archive_path, base / "project.tar")
            with tarfile.open(archive_path) as archive:
                files = {member.name: archive.extractfile(member).read()
                         for member in archive if member.isfile()}
            self.assertEqual(files, {"P/Проект.yaml": b"project", "P/CRM/Module.xbsl": b""})
            original = archive_path.read_bytes()
            with self.assertRaisesRegex(ValueError, "Архив уже существует"):
                write_tar(dump, projects)
            self.assertEqual(archive_path.read_bytes(), original)

    @unittest.skipUnless((REPO / "Dvizhok.xdump").exists() and (REPO / "Движок.tar").exists(),
                         "Требуются локальные xdump и эталонный tar")
    def test_supplied_dump_matches_reference_tar_byte_for_byte(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            extract_project(REPO / "Dvizhok.xdump", output)
            actual = {p.relative_to(output).as_posix(): p.read_bytes()
                      for p in output.rglob("*") if p.is_file()}
            with tarfile.open(REPO / "Движок.tar") as archive:
                expected = {unicodedata.normalize("NFC", member.name):
                            archive.extractfile(member).read()
                            for member in archive if member.isfile()}
            self.assertEqual(actual, expected)


if __name__ == "__main__":
    unittest.main()
