"""Stage 49 mechanism acceptance, independent of dump business scenarios."""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
import unittest
import yaml

from element_test.execution_plan import plan_execution
from element_test.model import analyze
from element_test.runtime import prepare_script, run_pure
from element_test.yaml_io import InputError

REPO = Path(__file__).resolve().parents[1]
SOURCE = '''метод Start()
;
метод Press(C: Кнопка, E: СобытиеПриНажатии)
    Count += 1
    Text = "${Объект.Name}:${Count}"
;
метод Rows(C: КомандаСПараметром<Массив<Entry.Lines>>, Values: Массив<Entry.Lines>)
    Total = 0
    для Value из Values
        Total += Value.Amount
    ;
;
метод Dynamic(E: СобытиеПриОткрытииПоСсылке)
    знч Label = новый Надпись(Значение = "added", Шрифт = новый АбсолютныйШрифт(Размер = 50, Полужирный = Истина))
    Компоненты.Group.Содержимое.Вставить(0, Label)
;
метод TimerStart()
    ПодключитьОбработчикТаймера(&Tick, 1с, Истина)
;
метод Tick()
    Count += 4
;
'''


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(value, allow_unicode=True, sort_keys=False))


def fixture(root, namespace='Alpha', name='Panel', source=SOURCE):
    write(root / 'Проект.yaml', {'ВидЭлемента': 'Проект', 'Имя': 'UI', 'Поставщик': 'Tests', 'Версия': '1.0', 'РежимСовместимости': '9.0'})
    write(root / namespace / 'Подсистема.yaml', {'ВидЭлемента': 'Подсистема', 'Имя': namespace, 'ОбластьВидимости': 'ВПроекте'})
    write(root / namespace / 'Entry.yaml', {'ВидЭлемента': 'Документ', 'Имя': 'Entry', 'ОбластьВидимости': 'ВПроекте',
        'Реквизиты': [{'Имя': 'Name', 'Тип': 'Строка'}], 'ТабличныеЧасти': [{'Имя': 'Lines', 'Реквизиты': [
            {'Имя': 'Amount', 'Тип': 'Число'}, {'Имя': 'Caption', 'Тип': 'Строка'}]}]})
    write(root / namespace / (name + '.yaml'), {'ВидЭлемента': 'КомпонентИнтерфейса', 'Имя': name, 'ОбластьВидимости': 'ВПроекте',
        'Свойства': [{'Имя': 'Enabled', 'Тип': 'Булево', 'ЗначениеПоУмолчанию': True}, {'Имя': 'Count', 'Тип': 'Число'},
                     {'Имя': 'Total', 'Тип': 'Число'}, {'Имя': 'Text', 'Тип': 'Строка'}],
        'Наследует': {'Тип': 'ФормаОбъекта<Entry.Объект>', 'Содержимое': {'Тип': 'Группа', 'Имя': 'Group', 'Содержимое': [
            {'Тип': 'Флажок', 'Имя': 'Toggle', 'Значение': '=Enabled'},
            {'Тип': 'ПолеВвода<Строка>', 'Имя': 'Input', 'Значение': '=Объект.Name', 'Доступность': '=Enabled'},
            {'Тип': 'Кнопка', 'Имя': 'Button', 'ПриНажатии': 'Press', 'Доступность': '=Enabled'},
            {'Тип': 'Надпись', 'Имя': 'Output', 'Значение': '=Text'},
            {'Тип': 'Надпись', 'Имя': 'Counter', 'Значение': '=Count'},
            {'Тип': 'Таблица<ИсточникДанныхМассив<Entry.Lines>>', 'Имя': 'Grid', 'ИспользоватьОтметкиСтрок': True,
             'Источник': {'Данные': '=Объект.Lines'}, 'Колонки': [
                {'Тип': 'СтандартнаяКолонкаТаблицы<Entry.Lines, Число>', 'Имя': 'Amount', 'Значение': '=RowData.Amount'}],
             'КомандыНадОтмеченнымиСтроками': {'Тип': 'ФрагментКомандногоИнтерфейса<КомандаСПараметром<Массив<Entry.Lines>>>',
                'Элементы': [{'Тип': 'КомандаСПараметром<Массив<Entry.Lines>>', 'Обработчик': 'Rows'}]}}]}}})
    if source is not None:
        (root / namespace / (name + '.xbsl')).write_text(source)


def config(namespace='Alpha', name='Panel'):
    return {'interface': {'schemaVersion': 1, 'form': {'name': name, 'namespace': namespace}},
        'context': {'Объект': {'Name': 'initial', 'Lines': [{'Amount': 3, 'Caption': 'a'}, {'Amount': 3, 'Caption': 'a'}, {'Amount': 8, 'Caption': 'b'}]}},
        'lifecycle': {'isNew': True}, 'observe': ['Объект.Name', 'Объект.Lines', 'Enabled', 'Count', 'Total', 'Text',
            'Компоненты.Input.Значение', 'Компоненты.Output.Значение', 'Компоненты.Counter.Значение']}


class InterfaceFixture(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name); self.root = self.base / 'project'; fixture(self.root)

    def plan(self, c=None):
        return plan_execution(self.root, analyze(self.root), c or config())


class InterfacePlanTest(InterfaceFixture):
    def test_semantic_handler_and_full_source_identity_without_expected(self):
        c = config(); c['expected'] = {'SECRET_ANSWER': 12345}
        c['sequence'] = [{'input': {'selector': {'role': 'ПолеВвода', 'binding': 'Объект.Name'}, 'value': 'changed'}},
                         {'invoke': {'selector': {'role': 'Кнопка'}}}]
        p = self.plan(c)
        self.assertEqual(p.interface['actions'][1]['method'], 'Press')
        self.assertEqual(p.interface['sourceHash'], __import__('hashlib').sha256((self.root / 'Alpha/Panel.yaml').read_bytes()).hexdigest())
        self.assertNotIn('SECRET_ANSWER', p.to_json())
        b = p.interface['bindings'][0]; a, z = b['position']['range']
        self.assertIn(b['expression'], (self.root / b['sourceFile']).read_text()[a:z])
        a, b = p.interface['actions'][1]['range']; self.assertIn('метод Press', SOURCE[a:b])
        prepare_script(self.root, analyze(self.root), c, self.base)
        text = '\n'.join(x.read_text() for x in self.base.glob('*.sbsl'))
        self.assertIn('Count += 1', text); self.assertNotIn('SECRET_ANSWER', text)
        # Renaming the handler and component does not change role/binding selection.
        path = self.root / 'Alpha/Panel.yaml'; path.write_text(path.read_text().replace('Press', 'Renamed').replace('Имя: Input', 'Имя: RenamedInput'))
        path = self.root / 'Alpha/Panel.xbsl'; path.write_text(SOURCE.replace('Press', 'Renamed'))
        c['observe'] = [p.replace('Компоненты.Input.', 'Компоненты.RenamedInput.') for p in c['observe']]
        self.assertEqual(self.plan(c).interface['actions'][1]['method'], 'Renamed')

    def test_no_module_required_and_all_base_types(self):
        (self.root / 'Alpha/Panel.xbsl').unlink()
        for typ in ('Форма', 'ПроизвольноеКлиентскоеПриложение', 'СтандартноеКлиентскоеПриложениеСРазделами'):
            write(self.root / 'Alpha/Standalone.yaml', {'ВидЭлемента': 'КомпонентИнтерфейса', 'Имя': 'Standalone',
                'Наследует': {'Тип': typ, 'Содержимое': {'Тип': 'ПолеВвода<Число>', 'Имя': 'Value'}}})
            c = {'interface': {'schemaVersion': 1, 'form': {'name': 'Standalone'}}, 'sequence': [
                {'input': {'selector': {'role': 'ПолеВвода'}, 'value': 7}}]}
            p = self.plan(c); self.assertEqual(p.symbols, []); self.assertEqual(p.entry.identity.declaration, '')
            prepare_script(self.root, analyze(self.root), c, self.base)
        c = config(); prepare_script(self.root, analyze(self.root), c, self.base)

    def test_invalid_inputs_and_unsupported_actions_are_explicit(self):
        cases = [ {'interface': None}, {'interface': {'schemaVersion': True, 'form': {'name': 'Panel'}}},
            {'interface': {'schemaVersion': 1, 'form': {'name': 'Panel'}, 'extra': True}}, {'context': []},
            {'sequence': [{'input': {'selector': {'role': 'ПолеВвода'}, 'value': 9}}]},
            {'sequence': [{'input': {'selector': {'name': 'Missing'}, 'value': 'x'}}]},
            {'sequence': [{'input': {'selector': {'name': 'Input'}, 'value': 5}}]},
            {'sequence': [{'input': {'selector': {'name': 'Amount'}, 'value': 5}}]},
            {'sequence': [{'selectRows': {'selector': {'name': 'Grid'}, 'indices': [True]}}]},
            {'sequence': [{'selectRows': {'selector': {'name': 'Grid'}, 'indices': [1, 1]}}]},
            {'sequence': [{'input': {'selector': {'name': 'Input'}, 'value': 'x', 'row': 1}}]},
            {'sequence': [{'invoke': {'selector': {'name': 'Button'}, 'rows': []}}]},
            {'sequence': [{'tableCommand': {'selector': {'name': 'Grid'}, 'command': 'Unknown'}}]},
            {'sequence': [{'snapshotInterface': False}]}, {'sequence': [{'unknown': True}]},
            {'sequence': [{'writeInterface': True}]}, {'target': {'method': 'Start'}}, {'sequence': []}]
        for change in cases:
            with self.subTest(change=change), self.assertRaises(InputError): self.plan({**config(), **change})

    def test_computed_inputs_and_cyclic_component_bindings_refused(self):
        path = self.root / 'Alpha/Panel.yaml'; text = path.read_text()
        path.write_text(text.replace('=Объект.Name', '=Объект.Name + "!"'))
        with self.assertRaisesRegex(InputError, 'прямую привязку'):
            self.plan({**config(), 'sequence': [{'input': {'selector': {'name': 'Input'}, 'value': 'x'}}]})
        path.write_text(text.replace('=Text', '=Компоненты.Output.Значение'))
        with self.assertRaises(InputError): self.plan()

    def test_nineteen_final_declarations_prepare_without_business_execution(self):
        from element_test.loader import open_project
        from hashlib import sha256
        archive = REPO / 'Dvizhok-final.xdump'; before = sha256(archive.read_bytes()).hexdigest()
        with open_project(archive) as root:
            model = analyze(root); forms = [e for e in model['elements'] if e['elementType'] == 'КомпонентИнтерфейса']
            self.assertEqual(len(forms), 19)
            for e in forms:
                c = {'interface': {'schemaVersion': 1, 'form': {'name': e['name'], 'namespace': e['namespace']}}}
                if e['properties']['Наследует']['Тип'].startswith('ФормаОбъекта<'): c['lifecycle'] = {'isNew': True}
                with self.subTest(form=e['name']):
                    p = plan_execution(root, model, c)
                    self.assertEqual(p.interface['execution'], 'not-checked')
                    self.assertFalse(p.symbols)
                    sandbox = self.base / e['name']; sandbox.mkdir()
                    prepare_script(root, model, c, sandbox)
        self.assertEqual(sha256(archive.read_bytes()).hexdigest(), before)


@unittest.skipUnless(os.getenv('ELEMENT_TEST_DOCKER_TESTS') == '1', 'Script Docker opt-in')
class InterfaceDockerTest(InterfaceFixture):
    def execute(self, c=None):
        fact = run_pure(self.root, analyze(self.root), c or config(), self.base)
        self.assertEqual(fact['status'], 'EXECUTED', fact)
        return fact['actual']

    def test_input_binding_recalculation_gate_original_handler_and_isolation(self):
        c = config(); c['sequence'] = [ {'input': {'selector': {'binding': 'Объект.Name', 'role': 'ПолеВвода'}, 'value': 'edited'}},
            {'invoke': {'selector': {'role': 'Кнопка'}}}, {'input': {'selector': {'name': 'Toggle'}, 'value': False}},
            {'input': {'selector': {'name': 'Input'}, 'value': 'denied'}}, {'invoke': {'selector': {'name': 'Button'}}}]
        actions = self.execute(c)['actions']
        self.assertEqual([a['outcome'] for a in actions], ['applied', 'applied', 'applied', 'blocked', 'blocked'])
        self.assertEqual(actions[0]['observation']['state']['Count'], 0)
        self.assertEqual(actions[1]['observation']['state']['Text'], 'edited:1')
        self.assertEqual(actions[1]['observation']['state']['Компоненты.Counter.Значение'], 1)
        self.assertEqual(actions[-1]['observation']['state']['Объект.Name'], 'edited')
        self.assertEqual(self.execute()['actions'][0]['observation']['state']['Count'], 0)
        # An unbound availability flag changed by the original source remains
        # live; invoking again must not use the old YAML default.
        path = self.root / 'Alpha/Panel.yaml'; text = path.read_text()
        declaration = yaml.safe_load(text)
        declaration['Наследует']['Содержимое']['Содержимое'][2].pop('Доступность')
        write(path, declaration)
        path = self.root / 'Alpha/Panel.xbsl'; path.write_text(SOURCE.replace('Count += 1', 'Count += 1\n    Компоненты.Button.Доступность = Ложь'))
        c = config(); c['sequence'] = [{'invoke': {'selector': {'name': 'Button'}}}] * 2
        actions = self.execute(c)['actions']
        self.assertEqual([a['outcome'] for a in actions], ['applied', 'blocked'])
        self.assertEqual(actions[-1]['observation']['state']['Count'], 1)

    def test_live_column_rows_selection_and_standard_commands(self):
        c = config(); c['sequence'] = [ {'input': {'selector': {'name': 'Amount'}, 'row': 1, 'value': 9}},
            {'selectRows': {'selector': {'name': 'Grid'}, 'indices': [1, 0]}},
            {'invoke': {'selector': {'role': 'КомандаСПараметром'}, 'rows': 'selected'}},
            {'tableCommand': {'selector': {'name': 'Grid'}, 'command': 'ПереместитьСтрокуВверх', 'row': 2}},
            {'tableCommand': {'selector': {'name': 'Grid'}, 'command': 'ДобавитьСтроку', 'value': {'Amount': 2, 'Caption': 'new'}}},
            {'selectRows': {'selector': {'name': 'Grid'}, 'indices': [0, 2]}},
            {'tableCommand': {'selector': {'name': 'Grid'}, 'command': 'Удалить'}}]
        actions = self.execute(c)['actions']
        self.assertEqual(actions[2]['observation']['state']['Total'], 12)
        self.assertEqual([r['Amount'] for r in actions[0]['observation']['state']['Объект.Lines']], [3, 9, 8])
        self.assertEqual([r['Amount'] for r in actions[3]['observation']['state']['Объект.Lines']], [3, 8, 9])
        self.assertEqual([r['Amount'] for r in actions[-1]['observation']['state']['Объект.Lines']], [8, 2])
        self.assertEqual(actions[0]['observation']['state']['Total'], 0)
        c['sequence'] = [{'input': {'selector': {'name': 'Amount'}, 'row': 90, 'value': 1}}]
        fact = run_pure(self.root, analyze(self.root), c, self.base)
        self.assertEqual(fact['status'], 'UNSUPPORTED', fact)

    def test_dynamic_label_tree_insertion_and_timer_binding(self):
        c = config(); c['observe'] = ['Count', 'Компоненты.Group.Содержимое', 'Компоненты.Counter.Значение']
        c['sequence'] = [{'method': 'Dynamic', 'args': [{}]}, {'method': 'TimerStart', 'args': []}, {'advanceTime': 1000}]
        c['clock'] = {'mode': 'fixed', 'date': '2028-02-29', 'time': '23:59:59', 'timezone': 'UTC'}
        c['executorLocale'] = 'en-US'; c['timers'] = {'onClose': 'cancel'}
        actions = self.execute(c)['actions']
        contents = actions[0]['observation']['state']['Компоненты.Group.Содержимое']
        self.assertEqual(contents[0]['Значение'], 'added'); self.assertEqual(contents[0]['Шрифт']['Размер'], 50)
        self.assertEqual(actions[-1]['observation']['state']['Компоненты.Counter.Значение'], 4)
        self.assertEqual(actions[-1]['events'][0]['state']['Компоненты.Counter.Значение'], 4)

    def test_constants_edit_write_and_detached_snapshots(self):
        write(self.root / 'Alpha/Settings.yaml', {'ВидЭлемента': 'НаборКонстант', 'Имя': 'Settings', 'Константы': [{'Имя': 'Title', 'Тип': 'Строка'}]})
        write(self.root / 'Alpha/SettingsForm.yaml', {'ВидЭлемента': 'КомпонентИнтерфейса', 'Имя': 'SettingsForm',
            'Наследует': {'Тип': 'ФормаЗаписиНабораКонстант<Settings.Запись>', 'Содержимое': {
                'Тип': 'ПолеВвода<Строка>', 'Имя': 'Title', 'Значение': '=Запись.Title'}}})
        c = {'interface': {'schemaVersion': 1, 'form': {'name': 'SettingsForm'}}, 'formEffects': {'write': True},
            'constants': {'Alpha::Settings': {'Title': 'saved'}}, 'observe': ['Запись.Title'], 'sequence': [
                {'snapshotInterface': True}, {'input': {'selector': {'name': 'Title'}, 'value': 'edited'}},
                {'writeInterface': True}, {'input': {'selector': {'name': 'Title'}, 'value': 'unsaved'}}]}
        actions = self.execute(c)['actions']
        self.assertEqual(actions[0]['observation']['state']['Запись.Title'], 'saved')
        self.assertEqual(actions[1]['observation']['constants']['Alpha::Settings']['Title'], 'saved')
        self.assertEqual(actions[2]['observation']['constants']['Alpha::Settings']['Title'], 'edited')
        self.assertEqual(actions[3]['observation']['constants']['Alpha::Settings']['Title'], 'edited')
        self.assertEqual(actions[3]['observation']['state']['Запись.Title'], 'unsaved')

    def test_single_row_command_mutates_the_actual_owner_row(self):
        path = self.root / 'Alpha/Panel.yaml'
        text = path.read_text().replace('КомандаСПараметром<Массив<Entry.Lines>>', 'КомандаСПараметром<Entry.Lines>').replace('Обработчик: Rows', 'Обработчик: Row')
        path.write_text(text)
        path = self.root / 'Alpha/Panel.xbsl'
        path.write_text(SOURCE + 'метод Row(C: КомандаСПараметром<Entry.Lines>, Value: Entry.Lines)\n    Value.Amount += 5\n;\n')
        c = config(); c['sequence'] = [{'selectRows': {'selector': {'name': 'Grid'}, 'indices': [1]}},
            {'invoke': {'selector': {'role': 'КомандаСПараметром'}}}]
        actions = self.execute(c)['actions']
        self.assertEqual([r['Amount'] for r in actions[1]['observation']['state']['Объект.Lines']], [3, 8, 8])
        self.assertEqual([r['Amount'] for r in actions[0]['observation']['state']['Объект.Lines']], [3, 3, 8])

    def test_direct_object_write_and_original_handler_write_then_open(self):
        c = config(); c['formEffects'] = {'write': True}; c['storage'] = {'idType': 'Ууид'}
        c['sequence'] = [{'input': {'selector': {'name': 'Input'}, 'value': 'persisted'}}, {'writeInterface': True}]
        result = self.execute(c); actions = result['result']['actions']
        self.assertFalse(actions[1]['observation']['isNew'])
        self.assertEqual(result['storage'][0]['value']['Name'], 'persisted')
        self.assertEqual(actions[0]['observation']['storage'], [])
        write(self.root / 'Alpha/OtherForm.yaml', {'ВидЭлемента': 'КомпонентИнтерфейса', 'Имя': 'OtherForm',
            'ОбластьВидимости': 'ВПроекте', 'Свойства': [{'Имя': 'Value', 'Тип': 'Число'}], 'Наследует': {'Тип': 'Форма'}})
        path = self.root / 'Alpha/Panel.xbsl'; path.write_text(SOURCE.replace('Count += 1', 'Записать()\n    OtherForm.Открыть(Value = 7)\n    Count += 1'))
        c['formEffects']['open'] = ['Alpha::OtherForm']; c['sequence'] = [{'invoke': {'selector': {'name': 'Button'}}}]
        result = self.execute(c)
        opening = result['result']['actions'][0]['observation']['openings'][0]
        self.assertEqual(opening, {'owner': 'Alpha::OtherForm', 'properties': {'Value': 7}})
        self.assertEqual(result['storage'][0]['value']['Name'], 'initial')

    def test_original_query_handler_uses_controlled_storage(self):
        path = self.root / 'Alpha/Panel.xbsl'
        path.write_text(SOURCE.replace('Count += 1', 'знч Q = Запрос{ВЫБРАТЬ КОЛИЧЕСТВО(*) КАК N ИЗ Entry}\n    Count = Q.Выполнить().Первый().N'))
        c = config(); c['storage'] = {'idType': 'Ууид', 'initial': [{'type': 'Alpha::Entry', 'value': {
            'Ссылка': {'Идентификатор': '12345678-1234-1234-1234-123456789abc'}, 'Name': 'stored'}}]}
        c['sequence'] = [{'invoke': {'selector': {'name': 'Button'}}}]
        result = self.execute(c)
        self.assertEqual(result['result']['actions'][0]['observation']['state']['Count'], 1)

    def test_postgres_write_independent_audit(self):
        from element_test.integration import run_integration
        c = config(); c['formEffects'] = {'write': True}
        c['storage'] = {'idType': 'Ууид', 'backend': 'postgres'}
        c['integration'] = {'backend': 'postgres', 'operation': 'metadata-storage'}
        c['sequence'] = [{'input': {'selector': {'name': 'Input'}, 'value': 'sql-stored'}}, {'writeInterface': True}]
        from unittest.mock import patch
        import secrets
        with patch.dict(os.environ, {'ELEMENT_TEST_INTEGRATION_PASSWORD': secrets.token_hex(24)}):
            fact = run_integration(c, analyze(self.root), self.base, root=self.root, enabled=True)
        self.assertEqual(fact['status'], 'EXECUTED', fact)
        self.assertEqual(fact['actual']['storage'][0]['value']['Name'], 'sql-stored')
        self.assertEqual(fact['actual']['sqlSnapshots'][0], [])
        self.assertEqual(fact['actual']['sqlSnapshots'][-1][0]['value']['Name'], 'sql-stored')

    def test_public_test_run_batch_grade_mutation_and_unavailable(self):
        c = {'interface': {'schemaVersion': 1, 'form': {'name': 'Panel'}}, 'lifecycle': {'isNew': True}, 'context': {},
             'observe': ['Count'], 'sequence': [{'invoke': {'selector': {'role': 'Кнопка'}}}]}
        # Independent facts for the limited fixture; answers never come from execution.
        bindings = {'Наследует/Содержимое/Содержимое/0/Значение': True,
            'Наследует/Содержимое/Содержимое/1/Значение': '', 'Наследует/Содержимое/Содержимое/1/Доступность': True,
            'Наследует/Содержимое/Содержимое/2/Доступность': True, 'Наследует/Содержимое/Содержимое/3/Значение': ':1',
            'Наследует/Содержимое/Содержимое/4/Значение': 1, 'Наследует/Содержимое/Содержимое/5/Колонки/0/Значение': []}
        expected = {'actions': [{'outcome': 'applied', 'observation': {'state': {'Count': 1}, 'bindings': bindings, 'selection': {}, 'isNew': True}}]}
        good = {'id': 'good', 'type': 'runtime', 'points': 2, **c, 'expected': expected}
        wrong = copy.deepcopy(good); wrong['id'] = 'wrong'; wrong['expected']['actions'][0]['observation']['state']['Count'] = 99
        unsupported = copy.deepcopy(good); unsupported['id'] = 'unsupported'; unsupported['points'] = 3
        unsupported['sequence'] = [{'tableCommand': {'selector': {'name': 'Grid'}, 'command': 'Unknown'}}]
        assignment = self.base / 'assignment.yaml'
        write(assignment, {'name': 'UI mechanism', 'checks': [good, wrong, unsupported]})
        for cmd in ('test', 'run'):
            out = self.base / cmd
            result = subprocess.run([str(REPO / 'bin/element-test'), cmd, '--project', str(self.root), '--assignment', str(assignment), '--output', str(out)], capture_output=True, text=True, timeout=120)
            self.assertEqual(result.returncode, 1, result.stderr)
            data = json.loads((out / 'result.json').read_text())
            self.assertEqual([v['status'] for v in data['checks']], ['PASS', 'FAIL', 'UNSUPPORTED'], data)
            self.assertEqual((data['score'], data['maxScore'], data['unavailablePoints']), (2, 4, 3))
        second = self.base / 'second'; shutil.copytree(self.root, second)
        path = second / 'Alpha/Panel.xbsl'; path.write_text(path.read_text().replace('Count += 1', 'Count += 2'))
        from element_test.batch import run_batch
        manifest = self.base / 'batch.json'; manifest.write_text(json.dumps({'schemaVersion': '1.0', 'assignment': str(assignment), 'assignmentId': 'ui49',
            'submissions': [{'studentId': 'one', 'project': str(self.root)}, {'studentId': 'two', 'project': str(second)}]}))
        self.assertEqual(run_batch(manifest, self.base / 'batch', workers=2), 1)
        batch = json.loads((self.base / 'batch/batch-result.json').read_text())
        for sub, statuses in zip(batch['submissions'], (['PASS', 'FAIL', 'UNSUPPORTED'], ['FAIL', 'FAIL', 'UNSUPPORTED'])):
            data = json.loads((self.base / 'batch' / sub['result']).read_text()); self.assertEqual([v['status'] for v in data['checks']], statuses)


if __name__ == '__main__': unittest.main()
