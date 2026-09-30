"""Generated contracts and method boundaries, independent of the student archive."""
from pathlib import Path
import subprocess
import tempfile
import unittest

from element_test.generated_types import ProjectTypes
from element_test.model import analyze
from element_test.runtime import decode_output, extract_method, prepare_script, REPO
from element_test.yaml_io import InputError


def model():
    return {"elements": [
        {"name": "Заказ", "namespace": "Продажи", "elementType": "Документ", "properties": {
            "Реквизиты": [{"Имя": "Номер", "Тип": "Строка"}],
            "ТабличныеЧасти": [{"Имя": "Товары", "Реквизиты": [
                {"Имя": "Номенклатура", "Тип": "Номенклатура.Ссылка?"},
                {"Имя": "Количество", "Тип": "Число", "ЗначениеПоУмолчанию": 1},
                {"Имя": "Сумма", "Тип": "Число"}]}]}},
        {"name": "Номенклатура", "namespace": "Товары", "elementType": "Справочник", "properties": {}},
        {"name": "НеНужен", "namespace": "", "elementType": "Документ", "properties": {}}]}


class GeneratedTypesTest(unittest.TestCase):
    def test_time_input_and_generated_object_field(self):
        data = model()
        data['elements'][0]['properties']['Реквизиты'].append({'Имя': 'Начало', 'Тип': 'Время'})
        types = ProjectTypes(data, 'Продажи')
        types.require('Заказ.Объект')
        self.assertIn('пер Начало: Время', types.definitions['Заказ.Объект'])
        for value in ('00:00', '09:30:40', '23:59:59.999'):
            with self.subTest(value=value):
                self.assertEqual(types.literal(value, 'Время'), f'новый Время("{value}")')
                self.assertIn(f'Начало = новый Время("{value}")',
                              types.literal({'Начало': value}, 'Заказ.Объект'))
        self.assertEqual(types.literal(None, 'Время?'), 'Неопределено')
        for value in ('24:00', '12:60', '12:30:60', '12:30:00.1234', '12:30.123',
                      '9:30', '12:30Z', '12:30${probe()}', 1230, None):
            with self.subTest(value=value), self.assertRaises(InputError):
                types.literal(value, 'Время')

    @unittest.skipUnless((REPO / "script_u_10.0.2_1/lib").is_dir(), "Требуется Script executor")
    def test_time_generated_object_compiles_in_9_0(self):
        data = model()
        data['elements'][0]['properties']['Реквизиты'].append({'Имя': 'Начало', 'Тип': 'Время'})
        types = ProjectTypes(data, 'Продажи')
        types.require('Заказ.Объект')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            imports = types.write(path)
            script = path / 'TimeProbe.sbsl'
            script.write_text(imports + '\nметод Скрипт()\n'
                '    знч Заказ = ' + types.literal({'Начало': '09:30:40.123'}, 'Заказ.Объект') + '\n'
                '    Консоль.Записать(Заказ.Начало.Час)\n'
                '    Консоль.Записать(Заказ.Начало.Минута)\n'
                '    Консоль.Записать(Заказ.Начало.Секунда)\n'
                '    Консоль.Записать(Заказ.Начало.Миллисекунда)\n;\n')
            result = subprocess.run([str(REPO / 'bin/script-runtime'), '-c', '9.0', str(script)],
                                    capture_output=True, text=True, timeout=30, cwd=path)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(result.stdout.splitlines(), ['9', '30', '40', '123'])

    @unittest.skipUnless((REPO / "script_u_10.0.2_1/lib").is_dir(), "Требуется Script executor")
    def test_runtime_time_argument_compiles_in_9_0(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            root = path / 'source'
            root.mkdir()
            (root / 'Проект.yaml').write_text('Имя: Часы\nРежимСовместимости: 9.0\n')
            (root / 'Clock.xbsl').write_text(
                'метод Час(Начало: Время): Число\n    возврат Начало.Час\n;\n')
            check = {'target': {'module': 'Clock', 'method': 'Час'}, 'args': ['09:30:40.123']}
            sandbox = path / 'generated'
            sandbox.mkdir()
            script = prepare_script(root, analyze(root), check, sandbox)
            self.assertIn('новый Время("09:30:40.123")', script.read_text())
            result = subprocess.run([str(REPO / 'bin/script-runtime'), '-c', '9.0', str(script)],
                                    capture_output=True, text=True, timeout=30, cwd=sandbox)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(decode_output(result.stdout)['actual'], 9)

    def test_private_type_in_package_is_visible_within_subsystem(self):
        data = {"elements": [{"name": "Вариант", "namespace": "Проверка::Пакет",
                              "elementType": "Перечисление", "visibility": "ВПодсистеме",
                              "properties": {"Элементы": [{"Имя": "Один"}]}}]}
        local = ProjectTypes(data, "Проверка")
        local.require("Вариант")
        self.assertIn("Вариант", local.definitions)
        with self.assertRaises(InputError):
            ProjectTypes(data, "Другая").require("Вариант")

    def test_union_variants_dates_and_strict_inputs(self):
        types = ProjectTypes(model())
        union = 'Заказ.Ссылка|Номенклатура.Ссылка|?'
        types.require(union)
        self.assertEqual(types.literal(None, union), 'Неопределено')
        self.assertIn('новый Заказ.Ссылка', types.literal(
            {'type': 'Заказ.Ссылка', 'value': {'Идентификатор': 'same-id'}}, union))
        for value in ({'Идентификатор': 'same-id'}, {'type': 'Строка', 'value': 'x'},
                      {'type': 'Заказ.Ссылка', 'value': {'НетПоля': 1}}, 5):
            with self.subTest(value=value), self.assertRaises(InputError):
                types.literal(value, union)
        types.require('Массив<Число|Строка|?>')
        self.assertIn('Неопределено', types.literal([1, 'text', None], 'Массив<Число|Строка|?>'))
        types.require('Заказ.Товары|Номенклатура.Ссылка')
        self.assertIn('Заказ.Товары', types.definitions)
        for invalid in ('Заказ.Ссылка|?', 'Строка|Строка', 'Строка||Число'):
            with self.subTest(invalid=invalid), self.assertRaises(InputError):
                ProjectTypes(model()).require(invalid)
        self.assertEqual(types.literal('2024-02-29T13:14:15', 'ДатаВремя'),
                         'новый ДатаВремя(2024, 2, 29, 13, 14, 15)')
        for date in ('2023-02-29T13:14:15', '2024-02-29T13:14:15Z', '${probe()}', None):
            with self.subTest(date=date), self.assertRaises(InputError):
                types.literal(date, 'ДатаВремя')

    def test_lazy_rows_references_and_invalid_inputs(self):
        types = ProjectTypes(model(), "Продажи")
        types.require("Массив<Заказ.Товары>")
        self.assertEqual(set(types.definitions), {"Заказ.Товары", "Номенклатура.Ссылка"})
        text = types.literal([{"Количество": 2, "Номенклатура": {"Идентификатор": '${probe()}'}}], "Массив<Заказ.Товары>")
        self.assertIn('\\${', text)
        for value in ([{"НетПоля": 1}], [{"Количество": True}], {}):
            with self.subTest(value=value), self.assertRaises(InputError):
                types.literal(value, "Массив<Заказ.Товары>")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            types.write(path)
            self.assertEqual({p.name for p in path.iterdir()}, {"Заказ.sbsl", "Номенклатура.sbsl"})
            self.assertIn("#требуется Номенклатура.sbsl", (path / "Заказ.sbsl").read_text())

    def test_ambiguous_and_unsupported_types_are_not_guessed(self):
        data = model()
        data["elements"].append({**data["elements"][0], "namespace": "Другие"})
        with self.assertRaises(InputError):
            ProjectTypes(data).require("Заказ.Товары")
        for name in ("Заказ.Нет", "НеНужен.НеизвестныеПараметры", "Массив<Число,Строка>"):
            with self.subTest(name=name), self.assertRaises(InputError):
                ProjectTypes(model()).require(name)

    def test_nested_blocks_preserve_exact_body_and_next_method(self):
        source = ('@Локально\nметод Итоги(Строки: Массив<Заказ.Товары>): Соответствие<Строка, Число>\n'
                  '    пер Сумма = 0\n    для Строка из Строки\n        если Строка.Количество > 0\n'
                  '            Сумма += Строка.Сумма // ;\n        ;\n    ;\n'
                  '    возврат {"Сумма": Сумма}\n;\nметод Другой(): Число\n    возврат 1\n;\n')
        method, parameters = extract_method(source, "Итоги")
        self.assertEqual(method, source[source.index("метод Итоги"):source.index("метод Другой")])
        self.assertEqual(parameters, ["Массив<Заказ.Товары>"])

    def test_module_cycles_are_rejected_before_writing_files(self):
        data = {"elements": [
            {"name": name, "namespace": "", "elementType": "Документ", "properties": {
                "ТабличныеЧасти": [{"Имя": "Строки", "Реквизиты": [
                    {"Имя": "Другой", "Тип": other + ".Ссылка?"}]}]}}
            for name, other in (("Первый", "Второй"), ("Второй", "Первый"))]}
        types = ProjectTypes(data)
        types.require("Первый.Строки")
        types.require("Второй.Строки")
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(InputError, "Циклическая"):
                types.write(Path(directory))
            self.assertEqual(list(Path(directory).iterdir()), [])

    @unittest.skipUnless((REPO / "script_u_10.0.2_1/lib").is_dir(), "Требуется Script executor")
    def test_minimal_object_data_reference_and_row_operations_in_executor(self):
        # Trusted fixture only; real student methods always execute in Docker.
        types = ProjectTypes(model(), "Продажи")
        for name in ("Заказ.Объект", "Заказ.Данные", "Заказ.Ссылка"):
            types.require(name)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            imports = types.write(path)
            script = path / "Probe.sbsl"
            script.write_text(imports + '\nметод Скрипт()\n'
                '    знч Документ = новый Заказ.Объект()\n'
                '    Документ.Товары.Добавить(новый Заказ.Товары(Сумма = 7))\n'
                '    знч Данные = новый Заказ.Данные(Номер = "001")\n'
                '    знч Ссылка = новый Заказ.Ссылка(Идентификатор = "order1")\n'
                '    Консоль.Записать(СериализацияJson.ЗаписатьОбъект({'
                '"rows": Документ.Товары.Размер(), "quantity": Документ.Товары[0].Количество, '
                '"number": Данные.Номер, "id": Ссылка.Идентификатор}))\n;\n')
            result = subprocess.run([str(REPO / "bin/script-runtime"), "-c", "9.0", str(script)],
                                    capture_output=True, text=True, timeout=30, cwd=path)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            import json
            self.assertEqual(json.loads(result.stdout), {"rows": 1, "quantity": 1, "number": "001", "id": "order1"})
