"""Teacher-controlled portability fixtures. Business sources execute unchanged in Docker."""
from pathlib import Path
import json

ID = '12345678-1234-4234-8234-123456789abc'
OTHER = 'abcdef12-1234-4234-8234-123456789abc'


def fixture(root, renamed=False):
    ns, obj, doc, reg, rows, value, label = (('Учет','Контрагенты','Заявка','История','Позиции','Итого','Название')
                                          if renamed else ('Data','Account','Journal','Facts','Entries','Amount','Label'))
    main, helper = ('Поток','Сервис') if renamed else ('Workflow','Worker')
    root.mkdir(parents=True, exist_ok=True)
    (root/'Проект.yaml').write_text('Имя: ' + ('Учёт' if renamed else 'Accounts') + '\nПоставщик: teacher\nРежимСовместимости: 9.0\n')
    for part in ('Entry','Service',ns): (root/part).mkdir(exist_ok=True)
    for name,kind in ((obj,'Справочник'),(doc,'Документ')):
        (root/ns/(name+'.yaml')).write_text('ВидЭлемента: '+kind+'\nИмя: '+name+'\nОбластьВидимости: ВПроекте\nРеквизиты:\n'
            '  - {Имя: '+label+', Тип: Строка}\n  - {Имя: '+value+', Тип: Число}\nТабличныеЧасти:\n'
            '  - Имя: '+rows+'\n    Реквизиты:\n      - {Имя: '+value+', Тип: Число}\n')
    (root/ns/(reg+'.yaml')).write_text('ВидЭлемента: РегистрСведений\nИмя: '+reg+'\nОбластьВидимости: ВПроекте\n'
        'Измерения:\n  - {Имя: Key, Тип: Строка}\nРесурсы:\n  - {Имя: '+value+', Тип: Число}\n')
    (root/ns/(doc+'.Объект.xbsl')).write_text('метод ПослеЗаписи(До: '+doc+'.Данные, ПараметрыЗаписи: '+doc+'.ПараметрыЗаписи)\n'
        '    если '+label+' == "handler-error"\n        выбросить новый ИсключениеВалидации("after-write")\n    ;\n;\n')
    (root/'Service'/(helper+'.xbsl')).write_text('@Глобально\nметод Validate(Text: Строка)\n'
        '    если Text.Пусто()\n        выбросить новый ИсключениеВалидации("empty label")\n    ;\n;\n'
        '@Глобально\nметод First(ID: Ууид, Text: Строка, N: Число): '+ns+'::'+obj+'.Ссылка\n'
        '    знч O = новый '+ns+'::'+obj+'.Объект(Ид = ID, '+label+' = Text, '+value+' = N)\n'
        '    O.'+rows+'.Добавить(новый '+ns+'::'+obj+'.'+rows+'('+value+' = N))\n'
        '    O.Записать()\n    возврат O.Ссылка\n;\n'
        '@Глобально\nметод Second(ID: Ууид, Text: Строка, N: Число)\n'
        '    знч R = новый '+ns+'::'+reg+'.НаборЗаписей()\n    R.Фильтр.Установить("bucket")\n'
        '    R.ДобавитьЗапись(Key = "bucket", '+value+' = N)\n    R.Записать()\n'
        '    знч O = новый '+ns+'::'+doc+'.Объект(Ид = ID, '+label+' = Text, '+value+' = N * 2)\n'
        '    O.'+rows+'.Добавить(новый '+ns+'::'+doc+'.'+rows+'('+value+' = N * 2))\n    O.Записать()\n;\n'
        '@Глобально\nметод Crash()\n    выбросить новый ИсключениеВалидации("late dependency")\n;\n')
    (root/'Entry'/(main+'.xbsl')).write_text('импорт Service::'+helper+' как Peer\n'
        'метод Apply(ID: Ууид, Text: Строка, N: Число, Mode: Число): '+ns+'::'+obj+'.Ссылка\n'
        '    Peer.Validate(Text)\n    исп Транзакции.Начать()\n'
        '    знч Ref = Peer.First(ID, Text, N)\n'
        '    если Mode == 1\n        возврат Ref\n    ;\n'
        '    Peer.Second(ID, Text, N)\n'
        '    если Mode == 2\n        Peer.Crash()\n    ;\n'
        '    если Mode == 3\n        попытка\n            Peer.Crash()\n'
        '        поймать E: ИсключениеНедопустимоеСостояние\n            выбросить новый ИсключениеНедопустимоеСостояние("wrong catch")\n'
        '        поймать E: ИсключениеВалидации\n            возврат Ref\n        ;\n    ;\n    возврат Ref\n;\n'
        'метод Branch(ID: Ууид): Число\n    если Истина\n        исп Транзакции.Начать()\n'
        '        Peer.First(ID, "branch", 5)\n    ;\n    Peer.Crash()\n    возврат 0\n;\n'
        'метод Rethrow(): Число\n    попытка\n        Peer.Crash()\n    поймать E: ИсключениеВалидации\n'
        '        выбросить E\n    ;\n    возврат 0\n;\n'
        'метод Swallow(ID: Ууид): Строка\n    попытка\n        Apply(ID, "valid", 2.5, 0)\n'
        '    поймать E: Исключение\n        возврат "swallowed"\n    ;\n    возврат "normal"\n;\n'
        'метод General(): Строка\n    попытка\n        Peer.Crash()\n    поймать E: Исключение\n'
        '        возврат E.Описание\n    ;\n    возврат "missing"\n;\n'
        'метод TypeOps(): Булево\n'
        '    пер Saved: ИсключениеВалидации|ИсключениеНедопустимоеСостояние = новый ИсключениеВалидации("typed")\n'
        '    попытка\n        выбросить Saved\n    поймать E: Исключение\n'
        '        возврат E это ИсключениеВалидации и (E как ИсключениеВалидации).Описание == "typed" и не (E это ИсключениеНедопустимоеСостояние)\n'
        '    ;\n;\n'
        'метод TryResource(ID: Ууид): Строка\n    попытка\n        исп Транзакции.Начать()\n'
        '        Peer.First(ID, "try", 5)\n        Peer.Crash()\n'
        '    поймать E: ИсключениеВалидации\n        возврат "caught"\n    ;\n    возврат "normal"\n;\n'
        'метод CatchResource(ID: Ууид): Строка\n    попытка\n        Peer.Crash()\n'
        '    поймать E: ИсключениеВалидации\n        исп Транзакции.Начать()\n'
        '        Peer.First(ID, "catch", 5)\n        возврат "caught"\n    ;\n    возврат "normal"\n;\n'
        'метод Lock(Ref: '+ns+'::'+obj+'.Ссылка): '+ns+'::'+obj+'.Объект?\n'
        '    возврат Ref.ЗагрузитьОбъект(Заблокировать = Истина)\n;\n'
        'метод Locked(Ref: '+ns+'::'+obj+'.Ссылка): '+ns+'::'+obj+'.Объект?\n'
        '    исп Транзакции.Начать()\n    возврат Lock(Ref)\n;\n'
        'метод State(): Булево\n    возврат Транзакции.ЕстьАктивная()\n;\n'
        'метод ScopeState(): Булево\n    исп Транзакции.Начать()\n    возврат State()\n;\n'
        'метод Loop(ID: Ууид): Число\n    для I из [1, 2, 3]\n        исп Транзакции.Начать()\n'
        '        Peer.First(ID, "loop", I)\n        если I == 1\n            продолжить\n        ;\n'
        '        прервать\n    ;\n    возврат 2\n;\n'
        'метод InactiveCatch(Ref: '+ns+'::'+obj+'.Ссылка): Строка\n    попытка\n'
        '        Lock(Ref)\n    поймать E: ИсключениеНетАктивнойТранзакции\n    возврат E.Описание\n'
        '    ;\n    возврат "wrong"\n;\n'
        'метод Nested(): Число\n    исп Транзакции.Начать()\n    возврат Inner()\n;\n'
        'метод Inner(): Число\n    исп Транзакции.Начать()\n    возврат 1\n;\n'
        'метод Unsupported(): Число\n    исп Handle = Транзакции.Начать()\n    Handle.Фиксировать()\n    возврат 1\n;\n')
    return {'ns':ns,'obj':obj,'doc':doc,'reg':reg,'rows':rows,'value':value,'label':label,'main':main}


def check(schema, method='Apply', args=None, transaction=False, sql=False):
    s=schema
    c={'target':{'module':s['main'],'namespace':'Entry','method':method},
       'args': [ID,'valid',2.5,0] if args is None else args, 'captureException':True,'timeout':'15s',
       'storage':{'backend':'postgres' if sql else 'memory','idType':'Ууид','transaction':transaction,
                  'registers':[s['ns']+'::'+s['reg']]}}
    if sql:c['integration']={'backend':'postgres','operation':'metadata-storage'}
    return c


def objects(actual):
    return {(x['type'],x['id']):x['value'] for x in actual['storage']}


if __name__ == '__main__':
    for renamed,name in ((False,'accounts'),(True,'renamed')):
        print(name,fixture(Path('tests/corpus/transactions')/name,renamed))
