"""Constructor dependencies are resolved without importing student modules."""
import copy
from pathlib import Path
import subprocess
import tempfile
import unittest

from element_test.assignment import load_assignment
from element_test.loader import open_project
from element_test.model import analyze
from element_test.generated_types import ProjectTypes
from element_test.runtime import REPO, body_type_references, constructor_types, method_closure, prepare_script
from element_test.yaml_io import InputError


class BodyTypesTest(unittest.TestCase):
    def fixture(self, root, body):
        source = ('метод Главный(): Число\n    возврат Помощник()\n;\n'
                  'метод Помощник(): Число\n' + body + '\n;\n'
                  'метод НеДостижим(): Число\n    возврат новый Неизвестный()\n;\n')
        (root / 'Модуль.xbsl').write_text(source)
        model = {'properties': {'Поставщик': 'Автор', 'Имя': 'Проект'},
                 'modules': [{'name': 'Модуль', 'namespace': 'A', 'moduleType': 'module',
                              'sourceFile': 'Модуль.xbsl'}],
                 'elements': [{'name': 'Док', 'namespace': 'B', 'elementType': 'Документ',
                               'properties': {'ТабличныеЧасти': [{'Имя': 'Строки', 'Реквизиты': [
                                   {'Имя': 'Количество', 'Тип': 'Число'}]}]}},
                              {'name': 'Лишний', 'namespace': 'A', 'elementType': 'Документ',
                               'properties': {}}]}
        return model, {'target': {'module': 'Модуль', 'method': 'Главный'}, 'args': []}

    def test_reachable_constructor_generates_minimal_contract_and_import(self):
        for spelling in ('Док.Строки', 'B::Док.Строки', 'Автор::Проект::B::Док.Строки',
                         'Массив<B::Док.Строки>'):
            with self.subTest(spelling=spelling), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                model, check = self.fixture(root, f'    знч Строка = новый {spelling}()\n    возврат 1')
                if '::' in spelling:
                    # Explicit qualification wins over a different local owner,
                    # including when the qualified type is inside a generic.
                    model['elements'].append({**copy.deepcopy(model['elements'][0]), 'namespace': 'A'})
                script = prepare_script(root, model, check, root).read_text()
                types = ProjectTypes(model, 'A')
                types.rename_collisions = True
                types.require(spelling)
                canonical = types.sbsl_type(spelling)
                owner = next(k.split('.')[0] for k in types.definitions)
                self.assertIn('#требуется ' + owner + '.sbsl', script)
                self.assertIn('новый ' + canonical + '()', script)
                self.assertIn('структура Строки', (root / (owner + '.sbsl')).read_text())
                self.assertFalse((root / 'Лишний.sbsl').exists())
                self.assertNotIn('метод НеДостижим', script)

    def test_strings_comments_and_interpolation(self):
        method = '''метод M(): Строка
    // новый Нет.Тип()
    /* новый Нет.Тип() */
    знч Текст = "новый Нет.Тип() \\${новый Нет.Тип()}"
    возврат "${новый Док.Строки()}"
;
'''
        self.assertEqual([t for _, _, t in constructor_types(method)], ['Док.Строки'])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model, check = self.fixture(root, '    // новый Нет.Тип()\n    знч Текст = "новый Нет.Тип()"\n    возврат 1')
            script = prepare_script(root, model, check, root).read_text()
            self.assertFalse((root / 'Док.sbsl').exists())
            self.assertIn('"новый Нет.Тип()"', script)

    def test_local_cast_nested_generic_and_union_use_exact_owner(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            body = ('    пер Строки: B::Док.Строки\n'
                    '    пер Набор: Соответствие<Строка, Массив<B::Док.Ссылка|Лишний.Ссылка>>\n'
                    '    знч Значение = Объект как B::Док.Ссылка\n'
                    '    возврат 1')
            model, check = self.fixture(root, body)
            model['elements'].append({**copy.deepcopy(model['elements'][0]), 'namespace': 'A'})
            source = (root / 'Модуль.xbsl').read_text()
            self.assertEqual([item[2] for item in body_type_references(source[source.index('метод Помощник'):source.index('метод НеДостижим')])],
                             ['B::Док.Строки', 'Соответствие<Строка,Массив<B::Док.Ссылка|Лишний.Ссылка>>', 'B::Док.Ссылка'])
            script = prepare_script(root, model, check, root).read_text()
            owner = next(root.glob('ТестТип*.sbsl')).stem
            self.assertIn('Соответствие<Строка, Массив<' + owner + '.Ссылка|Лишний.Ссылка>>', script)
            self.assertIn('как ' + owner + '.Ссылка', script)
            self.assertIn('структура Строки', (root / (owner + '.sbsl')).read_text())
            self.assertTrue((root / 'Лишний.sbsl').exists())

    def test_body_type_collision_and_unsupported_cycle(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model, check = self.fixture(root, '    пер Один: B::Док.Ссылка\n    пер Два: C::Док.Ссылка\n    возврат 1')
            model['elements'].append({**copy.deepcopy(model['elements'][0]), 'namespace': 'C'})
            prepare_script(root, model, check, root)
            self.assertEqual(len(list(root.glob('ТестТип*.sbsl'))), 2)
            model, check = self.fixture(root, '    пер Значение: B::Док.Объект\n    возврат 1')
            model['elements'][0]['properties']['Реквизиты'] = [{'Имя': 'Родитель', 'Тип': 'Док.Объект'}]
            with self.assertRaisesRegex(InputError, 'Циклическая зависимость'):
                prepare_script(root, model, check, root)

    @unittest.skipUnless((REPO / 'script_u_10.0.2_1/lib').is_dir(), 'Требуется Script executor')
    def test_local_project_type_compiles_in_executor(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model, check = self.fixture(root, '    пер Строка: B::Док.Строки\n    возврат 1')
            script = prepare_script(root, model, check, root)
            result = subprocess.run([str(REPO / 'bin/script-runtime'), '-c', '9.0', str(script)],
                                    cwd=root, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('"actual" : 1', result.stdout)

    def test_body_dependency_is_imported_by_context_module(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model, check = self.fixture(root, '    знч Строка = новый Док.Строки()\n    возврат 1')
            model['elements'].append({'name': 'Контекст', 'namespace': 'A',
                                     'elementType': 'Документ', 'properties': {}})
            model['modules'][0].update(name='Контекст.Объект', moduleType='object')
            check.update(target={'module': 'Контекст.Объект', 'method': 'Главный'}, context={})
            prepare_script(root, model, check, root)
            self.assertIn('#требуется Док.sbsl', (root / 'Контекст.sbsl').read_text())

    def test_unknown_unsupported_ambiguous_and_short_name_collision(self):
        for spelling in ('Нет.Строки', 'Док.Нет', 'Нет', 'C::Док.Строки'):
            with self.subTest(spelling=spelling), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                model, check = self.fixture(root, f'    знч Строка = новый {spelling}()\n    возврат 1')
                with self.assertRaises(InputError):
                    prepare_script(root, model, check, root)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model, check = self.fixture(root, '    знч Строка = новый Док.Строки()\n    возврат 1')
            model['elements'].append({**copy.deepcopy(model['elements'][0]), 'namespace': 'C'})
            with self.assertRaisesRegex(InputError, 'неоднознач'):
                prepare_script(root, model, check, root)
            model, check = self.fixture(root, '    знч A = новый B::Док.Строки()\n    знч B = новый C::Док.Строки()\n    возврат 1')
            model['elements'].append({**copy.deepcopy(model['elements'][0]), 'namespace': 'C'})
            prepare_script(root, model, check, root)
            self.assertEqual(len(list(root.glob('ТестТип*.sbsl'))), 2)
            # A cached definition must not hide a different local owner.
            model['modules'][0]['namespace'] = 'C'
            (root / 'Модуль.xbsl').write_text('метод Главный(): Число\n    знч A = новый B::Док.Строки()\n    знч B = новый Док.Строки()\n    возврат 1\n;\n')
            prepare_script(root, model, check, root)
            self.assertEqual(len(list(root.glob('ТестТип*.sbsl'))), 2)

    def test_receipt_original_body_and_archive_preserved(self):
        archive = REPO / 'Dvizhok.xdump'
        if not archive.is_file():
            self.skipTest('Требуется исходный архив')
        before = archive.read_bytes()
        with open_project(archive) as root, tempfile.TemporaryDirectory() as directory:
            model = analyze(root)
            module = next(m for m in model['modules'] if m['name'] == 'ПоступлениеТоваров.Объект')
            source = (root / module['sourceFile']).read_text()
            self.assertNotIn('ПослеЗаписи', source)
            for check in load_assignment(REPO / 'assignments/poc-receipt')['checks']:
                sandbox = Path(directory) / check['id']
                sandbox.mkdir()
                prepare_script(root, model, check, sandbox)
                generated = (sandbox / 'ПоступлениеТоваров.sbsl').read_text()
                for method, _ in method_closure(source, check['target']['method']):
                    self.assertIn(method, generated)
        self.assertEqual(archive.read_bytes(), before)
