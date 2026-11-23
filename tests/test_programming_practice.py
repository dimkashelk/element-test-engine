"""Acceptance of the assignment, flexible names and intentionally wrong programs."""
from contextlib import redirect_stdout
from copy import deepcopy
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shutil
from tempfile import TemporaryDirectory
import unittest

from element_test.model import analyze
from element_test.runtime import prepare_script, run_pure, execute_engine
from element_test.bridge import write_json
from element_test.yaml_io import InputError

REPO=Path(__file__).resolve().parents[1]
HERE=REPO/'assignments/programming-practice'
SPEC=importlib.util.spec_from_file_location('programming_checker',HERE/'check.py')
CHECK=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(CHECK)
REFERENCE=REPO/'tests/corpus/programming/reference'


def grade(root,checks,work):
    model=analyze(root)
    for c in checks:c['execution']=run_pure(root,model,c,work)
    write_json(work/'model.json',model);write_json(work/'assignment.json',{'checks':checks})
    return execute_engine('test',work/'model.json',work/'assignment.json',work)


class ProgrammingContractTest(unittest.TestCase):
    def test_independent_cases_and_optional_boundary(self):
        required=CHECK.cases();optional=CHECK.cases(True)
        self.assertEqual(138,len(required));self.assertEqual(142,len(optional))
        self.assertEqual(len(required),len({c['id'] for c in required}))
        self.assertFalse(any(c['id'].startswith('X07') for c in required))
        self.assertEqual(4,sum(c['id'].startswith('X07') for c in optional))
        byid={c['id']:c for c in required}
        self.assertEqual({'Строк':3,'Товаров':5,'Сумма':25.5},byid['P08.totals.duplicates']['expected'])
        self.assertEqual({'Календарных':3,'Рабочих':3,'Нерабочих':0},byid['P06.period.leap']['expected'])
        self.assertEqual('00000000-0000-4000-8000-000000000093',
                         byid['P07.order.1']['expected']['Сделка']['Идентификатор'])

    def test_reference_static_contract_and_all_sources_accounted_for(self):
        model=analyze(REFERENCE)
        checks=CHECK.structural(REFERENCE,model,{},True)
        self.assertTrue(checks)
        self.assertTrue(all(c['status']=='PASS' for c in checks),[c for c in checks if c['status']!='PASS'])
        self.assertEqual(len(checks),len({c['id'] for c in checks}))
        self.assertEqual(2,len(CHECK.binding_cases(model,{})))

    def test_missing_baseline_type_and_dead_helper_are_rejected(self):
        with TemporaryDirectory() as directory:
            root=Path(directory)/'source';shutil.copytree(REFERENCE,root)
            p=root/'CRM/ФизическиеЛица.Объект.xbsl'
            p.write_text(p.read_text().replace('ФИО = СформироватьФИО(Фамилия, Имя, Отчество)',
                'ФИО = "${Фамилия.Сократить()} ${Имя.Сократить()} ${Отчество.Сократить()}".Сократить()'))
            checks=CHECK.structural(root,analyze(root),{},False)
            self.assertTrue(any(c['status']=='FAIL' and c['id'].startswith('P.reuse.CRM') for c in checks))
            p.write_text(p.read_text().replace('@Обработчик',''))
            checks=CHECK.structural(root,analyze(root),{},False)
            self.assertTrue(any(c['status']=='FAIL' and c['id']=='P01.event' for c in checks))

    def test_alias_file_cannot_supply_answers(self):
        with TemporaryDirectory() as directory:
            p=Path(directory)/'aliases.json'
            for value in [{'expected':42},{'names':[]},{'owners':{'X':3}},{'names':{'X':{'Helper':''}}},
                          {'names':{'X':{'A':'Same','B':'Same'}}}]:
                p.write_text(json.dumps(value))
                with self.assertRaises(InputError):CHECK.aliases_config(p)
            for value in [{'names':{'X':{'Сегодня: Среда — рабочий день':'Ошибочный ответ'}}},
                          {'names':{'X':{'expected':'Result'}}}]:
                p.write_text(json.dumps(value))
                with self.assertRaises(InputError):CHECK.aliases_config(p)

    def test_auto_resolution_accepts_renamed_helpers_and_form(self):
        with TemporaryDirectory() as directory:
            root=Path(directory)/'source';shutil.copytree(REFERENCE,root)
            rename_reference(root)
            model=analyze(root);config=CHECK.resolve_aliases(model,{})
            self.assertEqual('ФорматироватьИмя',config['names']['CRM::ФизическиеЛица.Объект']['СформироватьФИО'])
            self.assertEqual('Продажи::РедакторЗаказа',config['owners']['Продажи::ЗаказФормаОбъекта'])
            checks=CHECK.structural(root,model,config,False)
            self.assertTrue(all(c['status']=='PASS' for c in checks),[c for c in checks if c['status']!='PASS'])

    def test_expected_values_do_not_enter_generated_student_program(self):
        model=analyze(REFERENCE);c=next(c for c in CHECK.cases() if c['id']=='P01.fio.full')
        c['expected']='SECRET_EXPECTATION_814713'
        with TemporaryDirectory() as directory:
            script=prepare_script(REFERENCE,model,c,Path(directory))
            for file in script.parent.rglob('*.sbsl'):
                self.assertNotIn(c['expected'],file.read_text())

    def test_missing_method_is_failure_not_executed(self):
        with TemporaryDirectory() as directory:
            root=Path(directory)/'source';output=Path(directory)/'report'
            shutil.copytree(REFERENCE,root)
            p=root/'CRM/ФизическиеЛица.Объект.xbsl'
            p.write_text(p.read_text().split('@Локально')[0])
            with redirect_stdout(io.StringIO()):
                CHECK.main(['--project',str(root),'--output',str(output),'--only','P01.fio.full'])
            evidence=json.loads((output/'runtime-evidence.json').read_text())
            self.assertEqual('FAIL',evidence[0]['execution']['status'])
            self.assertEqual('required_method_missing',evidence[0]['execution']['reasonCode'])

    def test_overload_adapter_changes_only_identifiers_and_keeps_original(self):
        source='''метод Text(): Строка
    возврат "prefix"
;
метод Text(Начало: Строка): Строка
    возврат "${Начало}: ${Text()}"
;
метод Entry(): Строка
    возврат Text("A")
;
'''
        with TemporaryDirectory() as directory:
            root=Path(directory)/'original';root.mkdir();p=root/'View.xbsl';p.write_text(source)
            lowered,audit=CHECK.lower_local_overloads(root,Path(directory)/'adapted')
            self.assertEqual(source,p.read_text())
            self.assertEqual(1,len(audit));self.assertEqual(4,len(audit[0]['edits']))
            changed=(lowered/'View.xbsl').read_text()
            self.assertIn('Text__ТестАрность0()',changed)
            self.assertIn('Text__ТестАрность1("A")',changed)
            self.assertTrue(all(e['original']=='Text' for e in audit[0]['edits']))


def rename_reference(root):
    changes={'СформироватьФИО':'ФорматироватьИмя','РассчитатьДни':'ПодсчитатьПериод','РассчитатьИтоги':'ИтогиВыбора'}
    for p in root.rglob('*.xbsl'):
        text=p.read_text()
        for old,new in changes.items():text=re.sub(r'\b'+old+r'\b',new,text)
        p.write_text(text)
    for suffix in ['.xbsl','.yaml']:
        p=root/'Продажи'/('ЗаказФормаОбъекта'+suffix)
        text=p.read_text().replace('ЗаказФормаОбъекта','РедакторЗаказа')
        p.unlink();(root/'Продажи'/('РедакторЗаказа'+suffix)).write_text(text)
    p=root/'Продажи/Заказ.yaml';p.write_text(p.read_text().replace('ЗаказФормаОбъекта','РедакторЗаказа'))
    for suffix in ['.xbsl','.yaml']:
        p=root/'Администрирование'/('ФормаПриветствие'+suffix)
        p.write_text(p.read_text().replace('КнопкаРассчитатьПриНажатии','ПоНажатиюКнопкиПериода'))


@unittest.skipUnless(os.environ.get('ELEMENT_TEST_DOCKER_TESTS')=='1','Docker behavior verification is opt-in')
class ProgrammingBehaviorTest(unittest.TestCase):
    def test_renamed_implementation_executes_without_source_rewriting(self):
        with TemporaryDirectory() as directory:
            root=Path(directory)/'source';shutil.copytree(REFERENCE,root);rename_reference(root)
            model=analyze(root);config=CHECK.resolve_aliases(model,{})
            ids={'P01.fio.no_middle','P06.period.week','P06.button.week','P08.totals.duplicates','P03.title.Заказ.saved'}
            checks=[CHECK.adapt(c,config) for c in CHECK.cases() if c['id'] in ids]
            result=grade(root,checks,Path(directory))
            self.assertEqual(['PASS']*5,[c['status'] for c in result['checks']],result)

    def test_mutations_of_student_programs_are_detected(self):
        mutations=[
            ('sum_product','Продажи/ЗаказФормаОбъекта.xbsl','СуммаТоваров += Элемент.Сумма',
             'СуммаТоваров += Элемент.Цена * Элемент.Количество','P08.totals.duplicates'),
            ('count_quantity','Продажи/ЗаказФормаОбъекта.xbsl','КоличествоСтрок += 1',
             'КоличествоСтрок += Элемент.Количество','P08.totals.duplicates'),
            ('exclusive_end','Администрирование/ФормаПриветствие.xbsl','пока ТекущаяДата <= ДатаКонец',
             'пока ТекущаяДата < ДатаКонец','P06.period.weekday'),
            ('hardcoded_workweek','Администрирование/ФормаПриветствие.xbsl','если Дни.Получить(ИмяДня)',
             'если Истина','P06.period.none'),
            ('no_trim','CRM/ФизическиеЛица.Объект.xbsl','Телефон = Телефон.Сократить()',
             '// Нормализация телефона удалена','P01.write.full'),
            ('wrong_base','Продажи/Заказ.Объект.xbsl','этот.Сделка = Основание',
             'этот.Сделка = Неопределено','P07.order.0'),
            ('copy_price_to_sum','Товары/Отгрузка.xbsl','Сумма = Элемент.Сумма',
             'Сумма = Элемент.Цена','L29.partial.one'),
            ('unsaved_archive','Продажи/ЗаказФормаОбъекта.xbsl','    Записать()','    // Запись удалена','P04.archive.Заказ.0')]
        evidence=[]
        for name,file,old,new,criterion in mutations:
            with self.subTest(mutation=name),TemporaryDirectory() as directory:
                root=Path(directory)/'source';shutil.copytree(REFERENCE,root)
                p=root/file;source=p.read_text();self.assertIn(old,source);p.write_text(source.replace(old,new))
                checks=[deepcopy(c) for c in CHECK.cases() if c['id']==criterion]
                self.assertEqual(1,len(checks))
                result=grade(root,checks,Path(directory));self.assertEqual('FAIL',result['checks'][0]['status'],result)
                evidence.append(dict(mutation=name,criterion=criterion,status='FAIL'))
        write_json(REPO/'result/programming-selftest/mutations.json',evidence)


if __name__=='__main__':unittest.main()
