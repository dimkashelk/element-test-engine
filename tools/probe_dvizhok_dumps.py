"""Bounded planning and isolated runtime probes; no full business acceptance."""
from copy import deepcopy
import json
from pathlib import Path
import sys

from element_test.execution_plan import plan_execution
from element_test.assignment import load_assignment
from element_test.model import analyze
from element_test.runtime import prepare_script, run_pure

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / 'result/dvizhok-dumps-assessment-2026-10-14'
CLOCK = {'mode': 'fixed', 'date': '2026-10-14', 'time': '12:00:00', 'timezone': 'UTC'}


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def ref(number):
    return {'Идентификатор': f'00000000-0000-4000-8000-{number:012d}'}


def main():
    evidence = []
    for dump in ('Dvizhok-2', 'Dvizhok-dev-final'):
        root = OUT / dump / 'sources'
        model = analyze(root)
        regs = [e['namespace'] + '::' + e['name'] for e in model['elements']
                if e['elementType'] in {'РегистрСведений', 'РегистрНакопления'}]
        for element in model['elements']:
            kind = {'КомпонентИнтерфейса': 'interface', 'Отчет': 'report', 'ПанельОтчетов': 'panel',
                    'ФрагментКомандногоИнтерфейса': 'navigation'}.get(element['elementType'])
            if not kind:
                continue
            config = {kind: {'schemaVersion': 1,
                      ('form' if kind == 'interface' else kind): {'name': element['name'], 'namespace': element['namespace']}},
                      'runtimeProfile': '9.3'}
            if kind == 'interface' and element['properties']['Наследует']['Тип'].startswith('ФормаОбъекта<'):
                config['lifecycle'] = {'isNew': True}
            if kind in {'report', 'panel'}:
                config['storage'] = {'idType': 'Ууид', 'registers': regs}
            if kind == 'panel':
                config.update(clock=CLOCK, executorLocale='en-US', timers={'onClose': 'cancel'})
            folder = OUT / dump / 'surface-probes' / element['name']
            folder.mkdir(parents=True, exist_ok=True)
            result = {'dump': dump, 'kind': kind, 'owner': element['name'], 'config': config,
                      'sourceHash': model['sourceHash'], 'execution': 'not-checked'}
            try:
                prepare_script(root, model, config, folder)
                result['generation'] = 'ready'
            except Exception as exc:
                result.update(generation='blocked', reason=str(exc), exceptionType=type(exc).__name__)
            evidence.append(result)
            if result['generation'] == 'blocked':
                print(dump, kind, element['name'], result['reason'], flush=True)
    save(OUT / 'surface-probes.json', evidence)

    root = OUT / 'Dvizhok-dev-final' / 'sources'
    model = analyze(root)
    direct = []
    for config in (
        {'target': {'namespace': 'Администрирование', 'module': 'ПроизвольноеПриветствие', 'method': 'ПолучитьНадпись'},
         'args': [], 'context': {'ТаймерКоличество': 111}},
        {'interface': {'schemaVersion': 1, 'form': {'name': 'ПроизвольноеПриветствие', 'namespace': 'Администрирование'}},
         'context': {'ТаймерКоличество': 111}, 'sequence': [{'method': 'ПолучитьНадпись', 'args': []}]},
    ):
        folder = OUT / 'direct-return-probes' / str(len(direct))
        folder.mkdir(parents=True, exist_ok=True)
        record = {'config': config, 'execution': 'not-checked'}
        try:
            prepare_script(root, model, config, folder)
            record['generation'] = 'ready'
        except Exception as exc:
            record.update(generation='blocked', reason=str(exc), exceptionType=type(exc).__name__)
        direct.append(record)
    save(OUT / 'direct-return-probes.json', direct)

    methods = []
    for dump in ('Dvizhok-2', 'Dvizhok-dev-final'):
        root = OUT / dump / 'sources'
        model = analyze(root)
        cov = json.loads((OUT / dump / 'coverage.json').read_text())
        for entry in cov['methods']:
            identity = entry['identity']
            module = next(m for m in model['modules'] if m['sourceFile'] == identity['sourceFile'])
            declaration = next(m for m in module['methods'] if m['name'] == identity['method'])
            notes = []
            if entry['directCriteria']:
                criterion = next((c for c in entry['directCriteria'] if c['planned']), entry['directCriteria'][0])
                config = deepcopy(next(c for c in load_assignment(Path(criterion['assignment']))['checks']
                                       if c['id'] == criterion['criterionId']))
                config.pop('expected', None)
                if identity['module'] == 'ФормаПриветствие' and identity['method'] == 'ПослеСоздания':
                    config['timers'] = {'onClose': 'cancel'}
                    notes.append('Added explicit timer contract to historical planning input')
                if config.get('context', {}).get('ТипОплаты') == 'retain payment':
                    config['context'].pop('ТипОплаты')
                    notes.append('Removed invalid historical enum sentinel; planning-only input, no expected or business test')
            else:
                def value(typ):
                    if typ == 'Булево': return True
                    if typ.endswith('.Ссылка'): return ref(1)
                    return {}
                args = [value(p['type']) for p in declaration['parameters']]
                config = {'runtimeProfile': '9.3'}
                if '.Объект' not in identity['module'] and declaration['returnType'] is None:
                    config.update(interface={'schemaVersion': 1, 'form': {
                        'name': identity['module'], 'namespace': identity['namespace']}},
                        sequence=[{'method': identity['method'], 'args': args}], context={})
                else:
                    config.update(target={k: identity[k] for k in ('namespace', 'module', 'method')}, args=args)
                    if '.Объект' in identity['module'] or identity['module'] == 'ПроизвольноеПриветствие':
                        config['context'] = {}
                if identity['module'] != 'ПроизвольноеПриветствие' and identity['method'] not in {'ОбновитьТекущееВремя', 'ОбновитьПриветствие'}:
                    registers = (['Товары::РегистрТовары', 'Товары::РегистрПоставщикиНоменклатуры']
                                 if identity['module'] == 'ПоступлениеТоваров.Объект' else
                                 ['Финансы::РегистрДенежныеСредства'] if '.Объект' in identity['module'] else
                                 ['Товары::РегистрЦеныНоменклатуры'] if 'ЦенаНоменклатуры' in identity['method'] else [])
                    config['storage'] = {'idType': 'Ууид', 'registers': registers, 'lifecycle': True}
                if identity['module'] == 'ПроизвольноеПриветствие' or identity['method'] in {'ОбновитьТекущееВремя', 'ОбновитьПриветствие'}:
                    config.update(clock=CLOCK, executorLocale='en-US', timers={'onClose': 'cancel'})
            folder = OUT / dump / 'typed-method-probes' / str(len(methods))
            folder.mkdir(parents=True, exist_ok=True)
            record = {'dump': dump, 'identity': identity, 'config': config, 'notes': notes,
                      'generation': 'ready', 'execution': 'not-checked', 'businessAssessment': 'not-checked'}
            try:
                prepare_script(root, model, config, folder)
            except Exception as exc:
                record.update(generation='blocked', reason=str(exc), exceptionType=type(exc).__name__)
                print(dump, identity['module'], identity['method'], str(exc), flush=True)
            methods.append(record)
        save(OUT / 'typed-method-probes.json', methods)

    if '--runtime' not in sys.argv:
        return
    root = OUT / 'Dvizhok-dev-final' / 'sources'
    model = analyze(root)
    results = json.loads((OUT / 'runtime-probes.json').read_text()) if '--focused' in sys.argv else []

    def execute(label, config, expected=None):
        if '--focused' in sys.argv and not label.startswith(('report-', 'timer-', 'empty-', 'compatibility-')):
            return
        config = deepcopy(config)
        config.setdefault('runtimeProfile', '9.3')
        config['timeout'] = '20s'
        folder = OUT / 'runtime-probes' / label
        folder.mkdir(parents=True, exist_ok=True)
        save(folder / 'config.json', config)
        result = run_pure(root, model, config, folder)
        record = {'label': label, 'sourceHash': model['sourceHash'], 'profile': config['runtimeProfile'],
                  'sourceCompatibility': '9.0', 'expected': expected, 'evidence': result,
                  'comparison': 'diagnostic Python assertion only; no SBSL grading or coverage acceptance'}
        if expected is not None and result['status'] == 'EXECUTED':
            record['matchesIndependentExpectation'] = result['actual'] == expected
        results.append(record)
        save(OUT / 'runtime-probes.json', results)
        actual = result.get('actual', {}).get('result')
        print(label, json.dumps({k: result.get(k) for k in ('status', 'reasonCode', 'message')}, ensure_ascii=False),
              'result=' + str(actual)[:150] if not isinstance(actual, dict) else '', flush=True)

    for method, owner, amounts in (
        ('ПолучитьСуммуПоступленияДС', 'ПоступлениеДенежныхСредств', [3, 8, 0]),
        ('ПолучитьМинимумРасходДС', 'РасходДенежныхСредств', [8, 3, 5]),
        ('ПолучитьКоличествоАрхивныхЗаказов', 'РасходДенежныхСредств', [8, 3, 5])):
        for filled in (False, True):
            storage = {'idType': 'Ууид', 'initial': [{'type': 'Финансы::' + owner,
                       'value': {'Ссылка': ref(i + 1), 'Сумма': n}} for i, n in enumerate(amounts)] if filled else []}
            config = {'target': {'module': 'ФормаПриветствие', 'namespace': 'Администрирование', 'method': method},
                      'args': [True] if method.endswith('Заказов') else [], 'storage': storage}
            expected = (0 if not filled else sum(amounts) if 'Сумму' in method else min(amounts))
            if method.endswith('Заказов'):
                expected = 0  # No orders seeded: a count of archived orders must be zero.
            execute(method + ('-filled' if filled else '-empty'), config, {'result': expected, 'storage': []} if not filled else None)

    for component, method, owner in (
        ('КнопкаВывестиСуммуПоступленияДС', 'ПолучитьСуммуПоступленияДС', 'ПоступлениеДенежныхСредств'),
        ('КнопкаВывестиМинимумРасходДС', 'ПолучитьМинимумРасходДС', 'РасходДенежныхСредств'),
        ('КнопкаВывестиКоличествоАрхивныхЗакаков', 'ПолучитьКоличествоАрхивныхЗаказов', 'РасходДенежныхСредств')):
        config = {'interface': {'schemaVersion': 1, 'form': {'name': 'ФормаПриветствие', 'namespace': 'Администрирование'}},
                  'storage': {'idType': 'Ууид', 'initial': [{'type': 'Финансы::' + owner, 'value': {'Ссылка': ref(1), 'Сумма': 7}}]},
                  'sequence': [{'invoke': {'selector': {'name': component}}}]}
        if method.endswith('Заказов'):
            config['sequence'].insert(0, {'input': {'selector': {'name': 'ПолеВводаКоличествоАрхивныхЗаказов'}, 'value': True}})
        execute('ui-' + component, config)

    stock = {'report': {'schemaVersion': 1, 'report': {'name': 'ОтчетОстаткиТоваров', 'namespace': 'Товары'}},
             'storage': {'idType': 'Ууид', 'registers': ['Товары::РегистрТовары'], 'initial': [
                 {'type': 'Товары::Номенклатура', 'value': {'Ссылка': ref(1), 'Наименование': 'No stock'}},
                 {'type': 'Товары::Номенклатура', 'value': {'Ссылка': ref(2), 'Наименование': 'Has stock'}}],
                 'initialRegisters': [{'type': 'Товары::РегистрТовары',
                     'filter': {'Регистратор': {'type': 'Товары::ПоступлениеТоваров.Ссылка', 'value': ref(3)}},
                     'rows': [{'Период': '2026-10-01T12:00:00', 'ВидЗаписи': 'Приход', 'Активность': True,
                               'Регистратор': {'type': 'Товары::ПоступлениеТоваров.Ссылка', 'value': ref(3)}, 'Номенклатура': ref(2),
                               'Склад': ref(4), 'Количество': 9}]}]}}
    execute('report-stock-left-join', stock)
    config = {'interface': {'schemaVersion': 1, 'form': {'namespace': 'Администрирование', 'name': 'ПроизвольноеПриветствие'}},
              'clock': CLOCK, 'executorLocale': 'en-US', 'timers': {'onClose': 'cancel'},
              'observe': ['ТаймерКоличество', 'Компоненты.НадписьОсталосьСекунд.Значение'],
              'sequence': [{'input': {'selector': {'name': 'ПолеВводаКоличество'}, 'value': 112}},
                           {'invoke': {'selector': {'name': 'КнопкаОбратныйОтсчет'}}}, {'advanceTime': 1000}]}
    execute('timer-111', config)
    for method in ('ПолучитьСуммуПоступленияДС', 'ПолучитьМинимумРасходДС', 'ПолучитьКоличествоАрхивныхЗаказов'):
        config = {'target': {'namespace': 'Администрирование', 'module': 'ФормаПриветствие', 'method': method},
                  'args': [True] if method.endswith('Заказов') else [], 'storage': {'idType': 'Ууид'},
                  'runtimeProfile': '9.0', 'observeFailure': True}
        execute('empty-observed-' + method, config)
    execute('compatibility-90-sum', {'target': {'namespace': 'Администрирование', 'module': 'ФормаПриветствие',
              'method': 'ПолучитьСуммуПоступленияДС'}, 'args': [], 'runtimeProfile': '9.0',
              'storage': {'idType': 'Ууид', 'initial': [{'type': 'Финансы::ПоступлениеДенежныхСредств',
                'value': {'Ссылка': ref(1), 'Сумма': 7}}]}})


if __name__ == '__main__':
    main()
