"""Direct client application calls use original source and independent returns."""
from contextlib import ExitStack
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from element_test.bridge import run_test
from element_test.execution_plan import plan_execution
from element_test.loader import open_project
from element_test.model import analyze
from element_test.runtime import prepare_script, run_pure
from element_test.yaml_io import InvalidTestError, UnsupportedSyntaxError

REPO = Path(__file__).resolve().parents[1]
TARGET = {'namespace': 'Администрирование', 'module': 'ПроизвольноеПриветствие',
          'method': 'ПолучитьНадпись'}


def check(count=1):
    return {'target': dict(TARGET), 'args': [], 'context': {'ТаймерКоличество': count},
            'observe': ['ТаймерКоличество']}


class ClientApplicationFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        stack = ExitStack()
        cls.addClassCleanup(stack.close)
        cls.projects = []
        for name in ('Dvizhok-2.xdump', 'Dvizhok-dev-final.xdump'):
            archive = REPO / name
            before = sha256(archive.read_bytes()).hexdigest()
            root = stack.enter_context(open_project(archive))
            cls.projects.append((name, root, analyze(root)))
            cls.addClassCleanup(cls.assert_archive_unchanged, archive, before)

    @staticmethod
    def assert_archive_unchanged(archive, before):
        if sha256(archive.read_bytes()).hexdigest() != before:
            raise AssertionError('Source archive changed: ' + str(archive))


class ClientApplicationPlanTest(ClientApplicationFixture):
    def test_direct_original_return_without_interface_or_timers(self):
        for name, root, model in self.projects:
            with self.subTest(dump=name), TemporaryDirectory() as output:
                c = {**check(), 'expected': {'SECRET_TEACHER_ANSWER': 937194}, 'branches': True}
                plan = plan_execution(root, model, c)
                self.assertEqual(plan.form['baseType'], 'ПроизвольноеКлиентскоеПриложение')
                self.assertFalse(plan.form['extended'])
                self.assertFalse(plan.interface)
                self.assertFalse(plan.timers)
                self.assertEqual(plan.entry.identity.declaration, 'ПолучитьНадпись')
                self.assertEqual([s.identity.declaration for s in plan.symbols], ['ПолучитьНадпись'])
                self.assertNotIn('SECRET_TEACHER_ANSWER', plan.to_json())
                prepare_script(root, model, c, Path(output))
                generated = '\n'.join(p.read_text() for p in Path(output).glob('*.sbsl'))
                self.assertIn('Контекст.ПолучитьНадпись()', generated)
                self.assertIn('ТаймерКоличество >= 11 и ТаймерКоличество <= 19', generated)
                self.assertNotIn('SECRET_TEACHER_ANSWER', generated)

    def test_declared_context_defaults_and_validation(self):
        for name, root, model in self.projects:
            with self.subTest(dump=name):
                c = check(); c['context'] = {}; c.pop('observe')
                self.assertTrue(plan_execution(root, model, c).form)
                for change in ({'context': {'Missing': 1}}, {'context': {'ТаймерКоличество': 'one'}},
                               {'observe': ['Missing']}, {'observe': ['ТаймерКоличество'] * 2},
                               {'lifecycle': {'isNew': True}}, {'args': [1]}):
                    with self.subTest(change=change), self.assertRaises(InvalidTestError):
                        plan_execution(root, model, {**check(), **change})

    def test_unrelated_base_types_still_require_their_contract(self):
        from element_test.form_context import describe_form
        element = next(e for e in self.projects[0][2]['elements'] if e['name'] == TARGET['module'])
        for base in ('СтандартноеКлиентскоеПриложениеСРазделами', 'ФормаЗаписиНабораКонстант<T.Запись>', 'Unknown'):
            other = deepcopy(element); other['properties']['Наследует']['Тип'] = base
            with self.subTest(base=base), self.assertRaises(UnsupportedSyntaxError):
                describe_form(other)


class ClientApplicationRuntimeTest(ClientApplicationFixture):
    def execute(self, root, model, c):
        with TemporaryDirectory() as output:
            fact = run_pure(root, model, c, Path(output))
        self.assertEqual(fact['status'], 'EXECUTED', fact)
        return fact['actual']

    def test_independent_direct_returns_and_fresh_context_on_both_dumps(self):
        # 111 documents the source's existing declension defect; it is not fixed
        # by the runtime adapter. All expectations are authored independently.
        cases = [(0, 'секунд'), (1, 'секунда'), (2, 'секунды'), (4, 'секунды'),
                 (5, 'секунд'), (11, 'секунд'), (19, 'секунд'), (21, 'секунда'),
                 (111, 'секунда')]
        for name, root, model in self.projects:
            for count, expected in cases:
                with self.subTest(dump=name, count=count):
                    self.assertEqual(self.execute(root, model, check(count)),
                                     {'return': expected, 'context': {'ТаймерКоличество': count}})

    def test_sequence_observes_return_and_state_between_direct_calls(self):
        for name, root, model in self.projects:
            with self.subTest(dump=name):
                c = check(2)
                c.update(executorLocale='en-US',
                         clock={'mode': 'fixed', 'date': '2026-10-14', 'time': '12:00:00', 'timezone': 'UTC'},
                         timers={'onClose': 'cancel'},
                         sequence=[{'method': 'ПолучитьНадпись', 'args': []},
                                   {'method': 'ОбратныйОтсчет', 'args': []},
                                   {'method': 'ПолучитьНадпись', 'args': []}])
                actions = self.execute(root, model, c)['actions']
                self.assertEqual(actions[0]['result'], {'return': 'секунды', 'context': {'ТаймерКоличество': 2}})
                self.assertEqual(actions[1]['result'], {'ТаймерКоличество': 1})
                self.assertEqual(actions[2]['result'], {'return': 'секунда', 'context': {'ТаймерКоличество': 1}})

    def test_public_grading_detects_wrong_expected_and_source_mutation(self):
        name, root, model = self.projects[0]
        good = {'id': 'good', 'type': 'runtime', 'points': 2, **check(1),
                'expected': {'return': 'секунда', 'context': {'ТаймерКоличество': 1}}}
        wrong = deepcopy(good); wrong['id'] = 'wrong'; wrong['expected']['return'] = 'секунд'
        with TemporaryDirectory() as output:
            base = Path(output)
            assignment = base / 'assignment.json'
            assignment.write_text(json.dumps({'name': 'Direct client returns', 'checks': [good, wrong]}, ensure_ascii=False))
            run_test(REPO / name, assignment, base / 'original')
            result = json.loads((base / 'original/result.json').read_text())
            self.assertEqual([c['status'] for c in result['checks']], ['PASS', 'FAIL'], result)
            self.assertEqual((result['score'], result['maxScore']), (2, 4))
            # Only a temporary extracted copy is mutated, never the archive.
            path = root / 'Администрирование/ПроизвольноеПриветствие.xbsl'
            original = path.read_text()
            try:
                path.write_text(original.replace('возврат "секунда"', 'возврат "changed"'))
                run_test(root, assignment, base / 'mutated')
                result = json.loads((base / 'mutated/result.json').read_text())
                self.assertEqual([c['status'] for c in result['checks']], ['FAIL', 'FAIL'], result)
            finally:
                path.write_text(original)


if __name__ == '__main__':
    unittest.main()
