"""Independent examples derived from the 30 lessons and practice requirements.

No expected value is obtained by running or translating a submission.
"""
from copy import deepcopy

PRODUCT = '00000000-0000-4000-8000-000000000091'
ID = '00000000-0000-4000-8000-000000000092'
OTHER = '00000000-0000-4000-8000-000000000093'
DATE = '2026-11-04T12:00:00'
WEEK = dict(zip(['Понедельник','Вторник','Среда','Четверг','Пятница','Суббота','Воскресенье'],
                [True,True,True,True,True,False,False]))
COMPANY = {'ПолноеНаименование':'Учебная компания','СокращенноеНаименование':'УК',
           'Телефон':'+7 000 111','ЮрАдрес':'Учебная улица 1'}
# Deliberately inconsistent price*quantity: sum the supplied Сумма, do not recalculate it.
ROWS = [dict(Номенклатура={'Идентификатор':PRODUCT}, Количество=1.25, Цена=2, Сумма=7.75),
        dict(Номенклатура=None, Количество=2.5, Цена=4, Сумма=10),
        dict(Номенклатура={'Идентификатор':PRODUCT}, Количество=1.25, Цена=2, Сумма=7.75)]
SETS = [('empty',[],0,0,0),('one',[ROWS[0]],1,1.25,7.75),
        ('subset',[ROWS[1]],1,2.5,10),('duplicates',ROWS,3,5,25.5)]
FORMS = [('CRM','Сделка','Сделка','новая'),('Продажи','Заказ','Заказ','новый'),
         ('Товары','Отгрузка','Отгрузка','новая'),
         ('Товары','ПоступлениеТоваров','Поступление товаров','новое'),
         ('Финансы','СчетНаОплату','Счёт на оплату','новый'),
         ('Финансы','ПоступлениеДенежныхСредств','Поступление денежных средств','новое'),
         ('Финансы','РасходДенежныхСредств','Расход денежных средств','новый')]


def ru(value):
    return str(value).replace('.', ',')


def runtime(ident, group, ns, module, method, args, expected, **kw):
    if module.endswith('ФормаОбъекта') and method != 'РассчитатьИтоги':
        kw.setdefault('context', {})
        kw.setdefault('lifecycle', {'isNew': True})
    return dict(id=ident, type='runtime', group=group, points=1,
                target=dict(namespace=ns,module=module,method=method), args=deepcopy(args),
                expected=deepcopy(expected), timeout='20s', executorLocale='ru-RU', trace=True,
                failureExplanation=f'{group}: фактический результат должен совпасть с ожидаемым; '
                                   'в отчёте приведены входные данные и оба результата.', **kw)


def value(name, flag=False):
    common={'Ссылка':{'Идентификатор':ID},'Дата':DATE,'Номер':'42','Клиент':None,'Архивный':flag}
    if name=='Заказ':
        return {**common,'СтатусЗаказа':None,'ДатаДоставки':DATE,'АдресДоставки':'Не менять',
                'Сделка':None,'Товары':deepcopy(ROWS)}
    return {**common,'СтатусСделки':'Новая','Сумма':13.75,'ДатаЗакрытия':DATE,
            'Участники':[], 'ПервичныйСпрос':[]}


def archive_case(ns,name,flag):
    obj=value(name,flag);owner=ns+'::'+name
    changed={**deepcopy(obj),'Архивный':not flag}
    stored=[dict(type=owner,id=ID,value=changed)]
    expected=dict(result=dict(result={'Объект':changed,
                           'ПредставлениеАрхивирования':'Архивировать' if flag else 'Вернуть из архива'},
                             openings=[],lifecycle=False,storage=stored),storage=stored)
    return runtime(f'P04.archive.{name}.{int(flag)}','P04',ns,name+'ФормаОбъекта',
                   'АрхивироватьОбработчик',[{}],expected,
                   context={'Объект':obj},lifecycle={'isNew':False},
                   formEffects={'write':True},storage={'backend':'memory','idType':'Ууид',
                                                       'initial':[dict(type=owner,value=obj)]},
                   observe=['Объект','ПредставлениеАрхивирования'])


def cases(include_optional=False):
    out=[]
    # Lecture constants are tested with inputs that cannot be guessed from real application data.
    for tag,data in [('filled',COMPANY),('empty',{k:'' for k in COMPANY})]:
        out.append(runtime('L09.company.'+tag,'L09','Администрирование','ДанныеКомпании',
                           'ПолучитьДанные',[],data,constants={'Администрирование::ДанныеКомпании':data}))
    for tag,days in [('weekday',WEEK),('none',{k:False for k in WEEK}),('all',{k:True for k in WEEK})]:
        out.append(runtime('L22.settings.'+tag,'L22','Администрирование','РабочиеДни','ПолучитьДанные',[],days,
                           constants={'Администрирование::РабочиеДни':days}))
    for tag,args,expected in [('full',[' Иванов ',' Иван ',' Иванович '],'Иванов Иван Иванович'),
                              ('no_middle',['Иванов','Иван',''],'Иванов Иван'),
                              ('blank',[' ','',' '],'')]:
        out.append(runtime('P01.fio.'+tag,'P01','CRM','ФизическиеЛица.Объект','СформироватьФИО',args,expected))
        context=dict(zip(['Фамилия','Имя','Отчество'],args));context.update(ФИО='Старое ФИО',Телефон=' +7 001 ',Почта=' test@example.org ')
        out.append(runtime('P01.write.'+tag,'P01','CRM','ФизическиеЛица.Объект','ПередЗаписью',[{},{}],
                           {'ФИО':expected,'Телефон':'+7 001','Почта':'test@example.org'},context=context,
                           observe=['ФИО','Телефон','Почта']))
    for ns,name,label,gender in FORMS:
        form=name+'ФормаОбъекта'
        for start in [False,True]:
            states=[{'РедактированиеДанных':not start,'ПредставлениеРедактированиеДанных':
                     'Разрешить редактирование реквизитов' if start else 'Запретить редактирование реквизитов'},
                    {'РедактированиеДанных':start,'ПредставлениеРедактированиеДанных':
                     'Запретить редактирование реквизитов' if start else 'Разрешить редактирование реквизитов'}]
            for state in states:
                state.update({'Объект.Номер':'Не менять','Объект.Дата':DATE})
            method='ПредставлениеРедактированиеДанныхОбработчик'
            out.append(runtime(f'P02.toggle.{name}.{int(start)}','P02',ns,form,method,[{}],{'actions':states},
                               context={'РедактированиеДанных':start,'Объект':{'Номер':'Не менять','Дата':DATE}},
                               observe=['РедактированиеДанных','ПредставлениеРедактированиеДанных','Объект.Номер','Объект.Дата'],
                               sequence=[dict(method=method,args=[{}]),dict(method=method,args=[{}])]))
        for tag,isnew,number in [('new',True,''),('saved',False,'042'),
                                 ('new_number',True,'manually-filled'),('saved_empty',False,'')]:
            expected=label+(' ('+gender+')' if isnew else ' (№'+number+')')
            out.append(runtime(f'P03.title.{name}.{tag}','P03',ns,form,'ПослеСоздания',[],
                               {'ЗаголовокФормы':expected},context={'Объект':{'Номер':number}},
                               lifecycle={'isNew':isnew},observe=['ЗаголовокФормы']))
    for ns,name in [('CRM','Сделка'),('Продажи','Заказ')]:
        for flag in [False,True]:out.append(archive_case(ns,name,flag))
    companytext='Вы работаете в Учебная компания; сокращённо: УК; телефон: +7 000 111; юридический адрес: Учебная улица 1'
    out.append(runtime('P05.company','P05','Администрирование','ФормаПриветствие','СформироватьТекстОрганизации',[COMPANY],companytext))
    for day in WEEK:
        for working in [True,False]:
            settings={k:working for k in WEEK}
            out.append(runtime(f'P05.day.{day}.{int(working)}','P05','Администрирование','ФормаПриветствие',
                               'ПолучитьТекстДня',[day,settings],f'Сегодня: {day} — '+('рабочий день' if working else 'выходной день')))
    for time,greeting in [('00:00:00','Доброй ночи'),('05:59:59','Доброй ночи'),('06:00:00','Доброе утро'),
                           ('11:59:59','Доброе утро'),('12:00:00','Добрый день'),('17:59:59','Добрый день'),
                           ('18:00:00','Добрый вечер'),('23:59:59','Добрый вечер')]:
        out.append(runtime('L14.greeting.'+time.replace(':',''),'L14','Администрирование','ФормаПриветствие','ПослеСоздания',[],
                           {'Приветствие':greeting,'Компоненты.НадписьМестоРаботы.Значение':companytext,
                            'Компоненты.НадписьТекущееВремя.Значение':'Сейчас: '+time,
                            'Компоненты.НадписьТипДня.Значение':'Сегодня: Среда — рабочий день',
                            'Компоненты.ДатаНачала.Значение':'2026-11-04'},context={},
                           constants={'Администрирование::ДанныеКомпании':COMPANY,'Администрирование::РабочиеДни':WEEK},
                           clock={'mode':'fixed','date':'2026-11-04','time':time,'timezone':'Europe/Moscow'},
                           observe=['Приветствие','Компоненты.НадписьМестоРаботы.Значение','Компоненты.НадписьТекущееВремя.Значение','Компоненты.НадписьТипДня.Значение','Компоненты.ДатаНачала.Значение']))
    periods=[('weekday','2026-11-04','2026-11-04',WEEK,1,1,0),
             ('weekend','2026-11-08','2026-11-08',WEEK,1,0,1),
             ('week','2026-11-02','2026-11-08',WEEK,7,5,2),
             ('two_weeks','2026-11-02','2026-11-15',WEEK,14,10,4),
             ('leap','2028-02-28','2028-03-01',WEEK,3,3,0),
             ('year','2026-12-31','2027-01-02',WEEK,3,2,1),
             ('all','2026-11-02','2026-11-08',{k:True for k in WEEK},7,7,0),
             ('none','2026-11-02','2026-11-08',{k:False for k in WEEK},7,0,7),
             ('reverse','2026-11-05','2026-11-04',WEEK,0,0,0)]
    for tag,start,end,settings,n,w,f in periods:
        out.append(runtime('P06.period.'+tag,'P06','Администрирование','ФормаПриветствие','РассчитатьДни',
                           [start,end,settings],{'Календарных':n,'Рабочих':w,'Нерабочих':f}))
        text=(f'Календарных: {n}; рабочих: {w}; нерабочих: {f}' if tag!='reverse'
              else 'Дата начала больше даты окончания периода')
        out.append(runtime('P06.button.'+tag,'P06','Администрирование','ФормаПриветствие',
                           'КнопкаРассчитатьПриНажатии',[{},{}],{'Компоненты.НадписьРезультат.Значение':text},
                           context={'Компоненты':{'ДатаНачала':{'Значение':start},'ДатаКонца':{'Значение':end}}},
                           constants={'Администрирование::РабочиеДни':settings},observe=['Компоненты.НадписьРезультат.Значение']))
    # Distinct bases prevent hardcoding or loading a different reference.
    for index,ref in enumerate([ID,OTHER]):
        client={'Идентификатор':PRODUCT} if index==0 else None
        out.append(runtime(f'P07.order.{index}','P07','Продажи','Заказ.Объект','ПриСозданииНаОсновании',
                           [{'Идентификатор':ref}],{'Клиент':client,'Сделка':{'Идентификатор':ref}},
                           context={},mocks={'objects':{'CRM::Сделка.Ссылка':{ref:{'Клиент':client}}}},observe=['Клиент','Сделка']))
        amount=17.75 if index==0 else 0
        out.append(runtime(f'P07.expense.{index}','P07','Финансы','РасходДенежныхСредств.Объект','ПриСозданииНаОсновании',
                           [{'Идентификатор':ref}],{'Клиент':client,'Сумма':amount,'Основание':{'Идентификатор':ref},'ТипОплаты':None},
                           context={},mocks={'objects':{'Финансы::СчетНаОплату.Ссылка':{ref:{'Контрагент':client,'Сумма':amount}}}},
                           observe=['Клиент','Сумма','Основание','ТипОплаты']))
        out.append(runtime(f'L20.receipt.{index}','L20','Финансы','ПоступлениеДенежныхСредств.Объект','ПриСозданииНаОсновании',
                           [{'Идентификатор':ref}],{'Клиент':client,'Сумма':amount,'Основание':{
                               'type':'Финансы::СчетНаОплату.Ссылка','value':{'Идентификатор':ref}}},
                           context={},mocks={'objects':{'Финансы::СчетНаОплату.Ссылка':{ref:{'Контрагент':client,'Сумма':amount}}}},
                           observe=['Клиент','Сумма','Основание']))
    for tag,rows,n,q,total in SETS:
        out.append(runtime('P08.totals.'+tag,'P08','Продажи','ЗаказФормаОбъекта','РассчитатьИтоги',[rows],
                           {'Строк':n,'Товаров':q,'Сумма':total}))
        out.append(runtime('P08.command.'+tag,'P08','Продажи','ЗаказФормаОбъекта','ИтогиОтмеченныхОбработчик',[{},rows],
                           {'Компоненты.НадписьИтогиОтмеченных.Значение':f'Строк: {n}; товаров: {ru(q)}; сумма: {ru(total)}'},
                           context={'Объект':{'Товары':ROWS}},observe=['Компоненты.НадписьИтогиОтмеченных.Значение']))
        out.append(runtime('L29.partial.'+tag,'L29','Товары','Отгрузка','СоздатьЧасть',[rows],
                           {'Ссылка':{'Идентификатор':''},
                            'Дата':'0001-01-01T00:00:00','Номер':'','Склад':None,'Товары':rows}))
        out.append(runtime('L17.full.'+tag,'L17','Товары','Отгрузка.Объект','ПриСозданииНаОсновании',
                           [{'Идентификатор':ID}],{'Товары':rows},context={},
                           mocks={'objects':{'Продажи::Заказ.Ссылка':{ID:{'Товары':rows}}}},observe=['Товары']))
        out.append(runtime('L18.invoice.'+tag,'L18','Финансы','СчетНаОплату.Объект','ПриСозданииНаОсновании',
                           [{'Идентификатор':ID}],{'Сумма':total,'Контрагент':{'Идентификатор':PRODUCT},'Основание':{'Идентификатор':ID}},
                           context={},mocks={'objects':{'Продажи::Заказ.Ссылка':{ID:{'Товары':rows,'Клиент':{'Идентификатор':PRODUCT}}}}},
                           observe=['Сумма','Контрагент','Основание']))
        # Request opening is observed independently of returned aggregate.
        opening={'owner':'Товары::ОтгрузкаФормаОбъекта','properties':{'СозданныйНаЧасти':True,
                 'ТоварыТабличнаяЧасть':rows,'КоличествоВыбранныхСтрок':n,'КоличествоТоваров':q,'СуммаВыбранныхТоваров':total}}
        out.append(runtime('P09.open.'+tag,'P09','Продажи','ЗаказФормаОбъекта','ОтгрузитьОбработчик',[{},rows],
                           {'result':{'result':{'ЗаголовокФормы':'Не менять'},'openings':[opening],'lifecycle':True,'storage':[]},'storage':[]},
                           context={'Объект':{'Товары':ROWS},'ЗаголовокФормы':'Не менять'},observe=['ЗаголовокФормы'],formEffects={'open':['Товары::ОтгрузкаФормаОбъекта']},
                           storage={'backend':'memory','idType':'Ууид'}))
        out.append(runtime('P09.target.'+tag,'P09','Товары','ОтгрузкаФормаОбъекта','ПослеСоздания',[],
                           {'ТекстИтоговЧасти':f'Выбрано строк: {n}; товаров: {ru(q)}; сумма: {ru(total)}'},
                           context={'СозданныйНаЧасти':True,'ТоварыТабличнаяЧасть':rows,
                                    'КоличествоВыбранныхСтрок':n,'КоличествоТоваров':q,'СуммаВыбранныхТоваров':total},
                           lifecycle={'isNew':True},observe=['ТекстИтоговЧасти']))
        if include_optional:
            out.append(runtime('X07.goods.'+tag,'X07','Товары','ПоступлениеТоваров.Объект','ПриСозданииНаОсновании',
                               [{'Идентификатор':ID}],{'Склад':{'Идентификатор':PRODUCT},'Товары':rows},context={},
                               mocks={'objects':{'Товары::Отгрузка.Ссылка':{ID:{'Склад':{'Идентификатор':PRODUCT},'Товары':rows}}}},
                               observe=['Склад','Товары']))
    for tag,rows,n,total in [('empty',[],0,0),('one',[{'Сумма':12.5}],1,12.5),('two',[{'Сумма':12.5},{'Сумма':7.75}],2,20.25)]:
        out.append(runtime('P08.demand.'+tag,'P08','CRM','СделкаФормаОбъекта','СуммаОтмеченногоСпросаОбработчик',[{},rows],
                           {'Компоненты.НадписьСуммаСпроса.Значение':f'Отмечено строк: {n}; сумма: {ru(total)}'},context={},
                           observe=['Компоненты.НадписьСуммаСпроса.Значение']))
        people=[{'Комментарий':'A','ВыигралСделку':i%2==0} for i,_ in enumerate(rows)]
        out.append(runtime('P08.people.'+tag,'P08','CRM','СделкаФормаОбъекта','ПосчитатьУчастниковОбработчик',[{},people],
                           {'Компоненты.НадписьКоличествоУчастников.Значение':f'Отмечено участников: {n}'},context={},
                           observe=['Компоненты.НадписьКоличествоУчастников.Значение']))
    return out
