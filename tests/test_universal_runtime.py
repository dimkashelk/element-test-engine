"""Portable original-code execution; expectations are independent of source answers."""
import copy
import json
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from element_test.execution_plan import plan_execution
from element_test.model import analyze
from element_test.runtime import prepare_script, decode_output, run_pure, REPO, execute_engine
from element_test.bridge import write_json
from element_test.generated_types import ProjectTypes
from element_test.yaml_io import InputError, InvalidTestError


class UniversalRuntimeTest(unittest.TestCase):
    def test_method_dependency_depth_has_no_python_stack_limit(self):
        from element_test.runtime import method_closure, project_method_closure, check_dependency_cycles
        count = 1200
        source = ''.join(f'метод M{i}(): Число\n    возврат ' +
                         (f'M{i+1}()' if i + 1 < count else '17') + '\n;\n' for i in range(count))
        self.assertEqual(len(method_closure(source, 'M0')), count)
        with TemporaryDirectory() as d:
            root = Path(d)
            (root/'Проект.yaml').write_text('Имя: Deep\nРежимСовместимости: 9.0\n')
            (root/'Main.xbsl').write_text(source)
            model = analyze(root)
            methods, _, _, _ = project_method_closure(root, model, model['modules'][0], 'M0')
            self.assertEqual(len(methods), count)
            plan = plan_execution(root, model, {'target': {'module': 'Main', 'method': 'M0'}})
            self.assertEqual(len(plan.symbols), count)
        graph = {str(i): {str(i + 1)} for i in range(count)}
        check_dependency_cycles(graph)
        graph[str(count)] = {'0'}
        with self.assertRaisesRegex(InputError, 'Циклическая'):
            check_dependency_cycles(graph)

    def fixture(self, root):
        (root/'Проект.yaml').write_text('Имя: Переносимость\nПоставщик: teacher\nРежимСовместимости: 9.0\n')
        (root/'A').mkdir(); (root/'B').mkdir()
        (root/'A/Main.xbsl').write_text('импорт B::Math как Helper\n'
            'конст Множитель = 2\n'
            'метод Run(X: Число): Число\n    если X < 0\n        возврат Crash()\n    ;\n'
            '    знч Callback = &Helper.C\n    возврат B(X) * Множитель + Callback(X)\n;\n'
            'метод B(X: Число): Число\n    возврат Helper.C(X) + 1\n;\n'
            'метод Crash(): Число\n    выбросить новый ИсключениеНедопустимоеСостояние("bad branch")\n;\n'
            'метод Unknown(): Число\n    возврат Missing()\n;\n'
            'метод Void()\n;\n')
        (root/'B/Math.xbsl').write_text('конст Offset = 3\n@Глобально\nметод C(X: Число): Число\n    возврат X + Offset\n;\n')
        return analyze(root)

    def execute(self, root, model, check, output):
        script = prepare_script(root, model, check, output)
        # This helper executes only trusted fixtures. Submission execution uses Docker.
        result = subprocess.run([str(REPO/'bin/script-runtime'), '-c','9.0',str(script)],
                                cwd=output,capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        value = decode_output(result.stdout)
        for file in Path('/tmp').glob('element-storage-*'):
            # Only own temporary files named in this generated session.
            session = output/'ТестСессия.sbsl'
            if session.exists() and str(file) in session.read_text():
                file.unlink(missing_ok=True)
        return value

    def test_chain_callback_constants_trace_and_wrong_dependency(self):
        with TemporaryDirectory() as d:
            p=Path(d); root=p/'source'; root.mkdir(); model=self.fixture(root)
            check={'target':{'module':'Main','namespace':'A','method':'Run'},'args':[4], 'trace':True}
            plan=plan_execution(root,model,check)
            self.assertEqual({s.identity.declaration for s in plan.symbols},{'Run','B','C','Crash'})
            self.assertIn('B/Math.xbsl',plan.declarations)
            gen=p/'one';gen.mkdir(); value=self.execute(root,model,check,gen)
            self.assertEqual(value['actual'],23)
            self.assertEqual([t['event'] for t in value['trace']],['enter','enter','enter','exit','exit','enter','exit','exit'])
            self.assertFalse(any('Crash' in t['symbol'] for t in value['trace']))
            self.assertEqual([t['value'] for t in value['trace'] if t['event']=='exit'],[7,8,7,23])
            source=root/'B/Math.xbsl';source.write_text(source.read_text().replace('X + Offset','X - Offset'))
            gen=p/'two';gen.mkdir(); altered=self.execute(root,analyze(root),check,gen)
            self.assertEqual(altered['actual'],5)
            write_json(p/'model.json',model);write_json(p/'assignment.json',{'checks':[{'id':'A','type':'runtime','points':1,'expected':23,
                'execution':{'status':'EXECUTED','actual':altered['actual']}}]})
            self.assertEqual(execute_engine('test',p/'model.json',p/'assignment.json',p)['checks'][0]['status'],'FAIL')
            with self.assertRaises(InputError):
                plan_execution(root,model,{'target':{'module':'Main','namespace':'A','method':'Unknown'}})

    def test_free_void_and_value_exception_keep_infrastructure_failure(self):
        with TemporaryDirectory() as d:
            p=Path(d);root=p/'source';root.mkdir();model=self.fixture(root)
            for method,args,expected in [('Void',[],{'result':None,'exception':None}),
                    ('Run',[1],{'result':14,'exception':None})]:
                gen=p/method;gen.mkdir()
                value=self.execute(root,model,{'target':{'module':'Main','namespace':'A','method':method},
                    'args':args,'captureException':True},gen)
                self.assertEqual(value['actual'],expected)
            gen=p/'except';gen.mkdir()
            value=self.execute(root,model,{'target':{'module':'Main','namespace':'A','method':'Run'},
                'args':[-1],'captureException':True},gen)
            self.assertIsNone(value['actual']['result'])
            self.assertIn('bad branch',value['actual']['exception']['message'])

    def test_uuid_and_owner_collision_fields_and_external_object(self):
        with TemporaryDirectory() as d:
            p=Path(d);root=p/'source';root.mkdir()
            (root/'Проект.yaml').write_text('Имя: Проверка\nРежимСовместимости: 9.0\n')
            for ns in ('One','Two'):
                (root/ns).mkdir();(root/ns/'Item.yaml').write_text('ВидЭлемента: Справочник\nИмя: Item\nОбластьВидимости: ВПроекте\nРеквизиты:\n  - {Имя: Количество, Тип: Число}\n')
                (root/ns/'Item.Объект.xbsl').write_text('метод Count(): Число\n    возврат Количество\n;\n')
            (root/'Main.xbsl').write_text('метод Run(A: One::Item.Объект, B: Two::Item.Объект, ID: Ууид): Строка\n'
                '    возврат "${A.Count() + B.Count()}:${ID.ВСтроку()}"\n;\n')
            gen=p/'generated';gen.mkdir()
            value=self.execute(root,analyze(root),{'target':{'module':'Main','method':'Run'},
                'args':[{'Количество':3},{'Количество':7},'12345678-1234-4234-8234-123456789abc']},gen)
            self.assertEqual(value['actual'],'10:12345678-1234-4234-8234-123456789abc')
            self.assertEqual(len(list(gen.glob('ТестТип*.sbsl'))),2)

    def test_multi_owner_local_structures_and_native_uuid_input(self):
        with TemporaryDirectory() as d:
            p=Path(d);root=p/'source';root.mkdir();model=self.fixture(root)
            (root/'A/Main.xbsl').write_text('структура Row\n    пер Amount: Число\n;\nметод Run(): Число\n    возврат B::Math.Value()\n;\n')
            (root/'B/Math.xbsl').write_text('структура Row\n    пер ID: Ууид\n    пер Amount: Число\n;\n@Глобально\nметод Value(): Число\n'
                '    знч R = новый Row(Amount = 9)\n    возврат R.Amount\n;\n')
            gen=p/'generated';gen.mkdir()
            self.assertEqual(self.execute(root,analyze(root),{'target':{'module':'Main','namespace':'A','method':'Run'}},gen)['actual'],9)
            types=ProjectTypes({'elements':[]});types.require('Ууид')
            for invalid in ('${Run()}', 'x', 1):
                with self.assertRaises(InvalidTestError):types.literal(invalid,'Ууид')

    def storage_fixture(self, root, name='Ledger', namespace='Biz', table='Rows'):
        (root/'Проект.yaml').write_text('Имя: CRUD\nРежимСовместимости: 9.0\n')
        (root/namespace).mkdir()
        (root/namespace/(name+'.yaml')).write_text('ВидЭлемента: Документ\nИмя: '+name+'\nРеквизиты:\n  - {Имя: Name, Тип: Строка}\n'
            '  - {Имя: Total, Тип: Число}\nТабличныеЧасти:\n  - Имя: '+table+'\n    Реквизиты:\n      - {Имя: Amount, Тип: Число}\n')
        (root/namespace/(name+'.Объект.xbsl')).write_text('метод ПередЗаписью(До: '+name+'.Данные, Параметры: '+name+'.ПараметрыЗаписи)\n'
            '    если Name.Пусто()\n        выбросить новый ИсключениеНедопустимоеСостояние("empty")\n    ;\n;\n')
        (root/namespace/(name+'.xbsl')).write_text('метод Write(ID: Строка, Name: Строка, Amount: Число): '+name+'.Ссылка\n'
            '    знч O = новый '+name+'.Объект(Ссылка = ПолучитьСсылку(ID), Name = Name, Total = Amount)\n'
            '    O.'+table+'.Добавить(новый '+name+'.'+table+'(Amount = Amount))\n    O.Записать()\n    возврат O.Ссылка\n;\n'
            'метод Read(ID: Строка): '+name+'.Объект?\n    возврат ПолучитьСсылку(ID).ЗагрузитьОбъект()\n;\n'
            'метод Fresh(ID: Строка): Число\n    знч Ref = ПолучитьСсылку(ID)\n    знч O = Ref.ЗагрузитьОбъект() как '+name+'.Объект\n'
            '    O.Total = 999\n    возврат (Ref.ЗагрузитьОбъект() как '+name+'.Объект).Total\n;\n'
            'метод Broken(ID: Строка)\n    Write(ID, "partial", 8)\n    выбросить новый ИсключениеНедопустимоеСостояние("rollback")\n;\n')
        return analyze(root)

    def test_metadata_crud_sequence_fresh_state_handlers_and_rollback(self):
        for name,ns,table in [('Ledger','Biz','Rows'),('Документ','Other','Позиции')]:
            with self.subTest(name=name),TemporaryDirectory() as d:
                p=Path(d);root=p/'source';root.mkdir();model=self.storage_fixture(root,name,ns,table)
                check={'target':{'module':name,'namespace':ns,'method':'Write'},'args':['one','A',1],
                       'storage':{'backend':'memory','transaction':True},'captureException':True,
                       'sequence':[{'method':'Write','args':['one','A',1]},{'method':'Write','args':['two','B',2]},
                        {'method':'Write','args':['one','Updated',3]},{'method':'Fresh','args':['one']},
                        {'method':'Write','args':['bad','',4]},{'method':'Broken','args':['partial']},
                        {'method':'Read','args':['one']}]}
                gen=p/'generated';gen.mkdir();value=self.execute(root,model,check,gen)['actual']
                self.assertEqual(value['result']['actions'][3]['result'],3)
                self.assertEqual(value['result']['actions'][6]['result']['Name'],'Updated')
                state={x['id']:x['value'] for x in value['storage']}
                self.assertEqual(set(state),{'one','two'})
                self.assertEqual((state['one']['Total'],state['two']['Total']),(3,2))
                self.assertEqual(state['one'][table],[{'Amount':3}])
                self.assertIn('empty',value['result']['actions'][4]['exception']['message'])
                self.assertIn('rollback',value['result']['actions'][5]['exception']['message'])

    def test_register_state_uses_declared_dimensions_fields_and_actual_rows(self):
        with TemporaryDirectory() as d:
            p=Path(d); root=p/'source';root.mkdir();(root/'N').mkdir()
            (root/'Проект.yaml').write_text('Имя: Регистры\nРежимСовместимости: 9.0\n')
            (root/'N/R.yaml').write_text('ВидЭлемента: РегистрСведений\nИмя: R\nОбластьВидимости: ВПроекте\n'
                'Измерения:\n  - {Имя: Bucket, Тип: Строка}\nРесурсы:\n  - {Имя: Value, Тип: Число}\n'
                'Реквизиты:\n  - {Имя: Flag, Тип: Булево}\n')
            (root/'Main.xbsl').write_text('метод Write(Bucket: Строка, Value: Число)\n'
                '    знч S = новый N::R.НаборЗаписей()\n    S.Фильтр.Установить(Bucket)\n'
                '    S.ДобавитьЗапись(Bucket, Value, Истина)\n    S.Записать()\n;\n')
            gen=p/'generated';gen.mkdir()
            actual=self.execute(root,analyze(root),{'target':{'module':'Main','method':'Write'},'args':['one',7.25],
                'storage':{'backend':'memory','registers':['N::R']}},gen)['actual']
            self.assertIsNone(actual['result'])
            self.assertEqual(actual['storage'][0]['type'],'N::R')
            self.assertEqual(json.loads(actual['storage'][0]['id']),{'Bucket':'one'})
            self.assertEqual(actual['storage'][0]['value'],[{'Bucket':'one','Value':7.25,'Flag':True}])

    def test_catalog_write_handler_receives_prior_snapshot(self):
        with TemporaryDirectory() as d:
            p = Path(d); root = p/'source'; root.mkdir()
            self.storage_fixture(root, 'Partner', 'Clients', 'Details')
            metadata = root/'Clients/Partner.yaml'
            metadata.write_text(metadata.read_text().replace('ВидЭлемента: Документ', 'ВидЭлемента: Справочник'))
            handler = root/'Clients/Partner.Объект.xbsl'
            handler.write_text('метод ПередЗаписью(До: Partner.Данные, Параметры: Partner.ПараметрыЗаписи)\n'
                '    если Name == "Updated" и До.Total != 1\n'
                '        выбросить новый ИсключениеНедопустимоеСостояние("wrong prior snapshot")\n    ;\n;\n')
            gen = p/'generated'; gen.mkdir()
            actual = self.execute(root, analyze(root), {'target': {'module': 'Partner', 'namespace': 'Clients', 'method': 'Write'},
                'args': ['one', 'Initial', 1],
                'storage': {'backend': 'memory', 'transaction': True}, 'captureException': True,
                'sequence': [{'method': 'Write', 'args': ['one', 'Initial', 1]},
                             {'method': 'Write', 'args': ['one', 'Updated', 3]}]}, gen)['actual']
            self.assertIsNone(actual['result']['actions'][1]['exception'])
            self.assertEqual(actual['storage'][0]['value']['Total'], 3)

    def test_balance_query_ast_resolves_metadata_and_rejects_other_shapes(self):
        from element_test.query_plan import parse_balance_query, balance_sql
        data={'elements':[{'name':'Stock','namespace':'N','elementType':'РегистрНакопления','properties':{
            'Измерения':[{'Имя':'Product','Тип':'Item.Ссылка?'},{'Имя':'Place','Тип':'Site.Ссылка?'}],
            'Ресурсы':[{'Имя':'Amount','Тип':'Число'}]}}]}
        c=ProjectTypes(data,'N')
        query='ВЫБРАТЬ S.Product КАК P, S.AmountОстаток КАК Total ИЗ N::Stock.Остатки КАК S ГДЕ S.Product В (%{Products})'
        ast=parse_balance_query(query,c)
        self.assertEqual((ast.register,ast.dimension,ast.resource,ast.parameter),('N::Stock','Product','Amount','Products'))
        sql=balance_sql(ast,['Product','Place'])
        self.assertIn("value->'Product'",sql);self.assertIn('type=&register',sql)
        for text in (query.replace('AmountОстаток','AmountОборот'),query+' И S.Place = 1', query.replace('S.Product В','S.Product =')):
            with self.assertRaises(InputError):parse_balance_query(text,c)


if __name__=='__main__':unittest.main()

@unittest.skipUnless(os.environ.get('ELEMENT_TEST_DOCKER_TESTS') == '1', 'Docker runtime is opt-in')
class UniversalDockerTest(unittest.TestCase):
    def test_original_chain_trace_and_unresolved_boundary_in_docker(self):
        helper=UniversalRuntimeTest()
        with TemporaryDirectory() as d:
            p=Path(d);root=p/'source';root.mkdir();model=helper.fixture(root)
            check={'target':{'module':'Main','namespace':'A','method':'Run'},'args':[4],'trace':True}
            value=run_pure(root,model,check,p)
            self.assertEqual(value['status'],'EXECUTED',value)
            self.assertEqual(value['actual'],23)
            self.assertEqual(len(value['trace']),8)
            unavailable=run_pure(root,model,{'target':{'module':'Main','namespace':'A','method':'Unknown'}},p)
            self.assertEqual(unavailable['status'],'UNSUPPORTED')
            self.assertEqual(run_pure(root,model,check,p)['actual'],23)


@unittest.skipUnless(os.environ.get('ELEMENT_TEST_INTEGRATION_TESTS') == '1', 'SQL storage is opt-in')
class UniversalSqlTest(unittest.TestCase):
    def test_metadata_sql_publication_audit_backend_failure_and_isolation(self):
        import secrets
        from unittest.mock import patch
        from element_test.integration import run_integration
        helper=UniversalRuntimeTest()
        with TemporaryDirectory() as d, patch.dict(os.environ,{'ELEMENT_TEST_INTEGRATION_PASSWORD':secrets.token_hex(24)}):
            p=Path(d);root=p/'source';root.mkdir();model=helper.storage_fixture(root)
            check={'target':{'module':'Ledger','namespace':'Biz','method':'Write'},'args':['one','Independent',4.25],
                   'storage':{'backend':'postgres','transaction':True},'captureException':True,
                   'integration':{'backend':'postgres','operation':'metadata-storage'}}
            for fail in (False,True,False):
                value=run_integration(check,model,p,enabled=True,root=root,inject_failure=fail)
                self.assertTrue(value['integration']['cleanup'],value)
                if fail:
                    self.assertEqual(value['status'],'ERROR',value)
                    self.assertEqual(value['storageEvidence'],[])
                else:
                    self.assertEqual(value['status'],'EXECUTED',value)
                    self.assertEqual(value['actual']['storage'],[{'type':'Biz::Ledger','id':'one','value':{
                        'Ссылка':{'Идентификатор':'one'},'Name':'Independent','Total':4.25,'Rows':[{'Amount':4.25}]}}])
