"""Detailed balance/turnover model with nullable boundary rows, run in Script.

At equal timestamps movements are ordered by nominal registrar identity, then
stored row index. This explicit model order does not claim native database order.
"""
from .query_sources import bucket


def detailed_totals_loader(q, c, required, extra):
    lit = lambda v: c.literal(v, 'Строка')
    by_record = q.totals_periodicity == 'Запись' or any(f in extra for f in ('Индекс', 'НомерСтроки'))
    by_registrar = by_record or q.totals_periodicity == 'Регистратор' or 'Регистратор' in extra
    time_fields = list(dict.fromkeys([f for f in extra if f.startswith('Период')] +
                                    (['ПериодЗаписи'] if q.totals_periodicity == 'Авто' else ['Период'])))
    detail = (['Регистратор'] if by_registrar else []) + (['Индекс', 'НомерСтроки'] if by_record else [])
    dimensions = [f.name for f in q.dimensions]
    start = 'П'+str(q.period_slot) if q.period_slot is not None else 'Неопределено'
    end = 'П'+str(q.end_slot) if q.end_slot is not None else 'Неопределено'
    def line(text, level=2):
        return '    '*level + text + '\n'
    def dates(row, value):
        return ''.join(line(row+'.Вставить('+lit(f)+', '+bucket(value, f[6:] if f != 'Период' else 'Записи')+'.ВСтроку())', 3) for f in time_fields)
    def group_key(row):
        names = dimensions + time_fields + detail + ['_Boundary'] + (['_RegistrarOrder'] if by_registrar else [])
        return 'СериализацияJson.ЗаписатьОбъект({'+', '.join(lit(f)+': '+row+'['+lit(f)+']' for f in names)+'})'
    # Source loading always adds bucket/index metadata, including hidden
    # registrar tags needed for equal-ID nominal identities.
    s = line('для Движение из Исходные')
    s += line('если не Движение.СодержитКлюч("__RegistrarTag")', 3)
    s += line('Движение.Вставить("__RegistrarTag", Неопределено)', 4)
    s += line('Движение.Вставить("__RegistrarValue", Неопределено)', 4) + line(';', 3)
    s += line('Движение.Вставить("_RegistrarOrder", СериализацияJson.ЗаписатьОбъект({"type": Движение["__RegistrarTag"], "value": Движение["Регистратор"]}))', 3)
    s += line(';') + line('знч Группы = новый Соответствие<Строка, Соответствие<Строка, Объект?>>()')
    s += line('для Движение из Исходные') + line('если не (Движение["Активность"] как Булево)', 3) + line('продолжить', 4) + line(';', 3)
    s += line('знч Период = новый ДатаВремя(Движение["Период"] как Строка)', 3)
    if start != 'Неопределено' and end != 'Неопределено':
        s += line('если '+start+' != Неопределено и '+end+' != Неопределено и ('+start+' как ДатаВремя) > ('+end+' как ДатаВремя)', 3) + line('продолжить', 4) + line(';', 3)
    if end != 'Неопределено':
        s += line('если '+end+' != Неопределено и Период > ('+end+' как ДатаВремя)', 3) + line('продолжить', 4) + line(';', 3)
    if q.complement == 'ЗаписиИГраницыПериода':
        for bound, rank in ((start, -1), (end, 1)):
            if bound == 'Неопределено':
                continue
            s += line('если '+bound+' != Неопределено', 3) + line('знч Граница = новый Соответствие<Строка, Объект?>()', 4)
            for f in dimensions:
                s += line('Граница.Вставить('+lit(f)+', Движение['+lit(f)+'])', 4)
            s += dates('Граница', '('+bound+' как ДатаВремя)')
            for f in detail + ['__RegistrarTag', '__RegistrarValue']:
                s += line('Граница.Вставить('+lit(f)+', Неопределено)', 4)
            s += line('Граница.Вставить("_RegistrarOrder", "")', 4) + line('Граница.Вставить("_Boundary", '+str(rank)+')', 4)
            s += line('Граница.Вставить("_Time", ('+bound+' как ДатаВремя).ВСтроку())', 4)
            s += line('Группы.Вставить('+group_key('Граница')+', Граница)', 4) + line(';', 3)
    if start != 'Неопределено':
        s += line('если '+start+' != Неопределено и Период < ('+start+' как ДатаВремя)', 3) + line('продолжить', 4) + line(';', 3)
    s += line('знч Новая = новый Соответствие<Строка, Объект?>()', 3)
    for f in dimensions + detail + ['__RegistrarTag', '__RegistrarValue', '_RegistrarOrder']:
        s += line('Новая.Вставить('+lit(f)+', Движение['+lit(f)+'])', 3)
    s += dates('Новая', 'Период') + line('Новая.Вставить("_Time", Период.ВСтроку())', 3) + line('Новая.Вставить("_Boundary", 0)', 3)
    s += line('Группы.Вставить('+group_key('Новая')+', Новая)', 3) + line(';')
    s += line('для Снимок из Группы.Значения()')
    for resource in q.resources:
        for suffix in ('Оборот', 'Приход', 'Расход', 'НачальныйОстаток', 'КонечныйОстаток'):
            s += line('Снимок.Вставить('+lit(resource.name+suffix)+', 0)', 3)
    s += line('знч ВремяГруппы = новый ДатаВремя(Снимок["_Time"] как Строка)', 3)
    s += line('знч Граница = Снимок["_Boundary"] как Число', 3)
    s += line('для Движение из Исходные', 3)
    equal = ' и '.join('СериализацияJson.ЗаписатьОбъект(Движение['+lit(f)+']) == СериализацияJson.ЗаписатьОбъект(Снимок['+lit(f)+'])' for f in dimensions) or 'Истина'
    s += line('если не (Движение["Активность"] как Булево) или не ('+equal+')', 4) + line('продолжить', 5) + line(';', 4)
    s += line('знч ВремяДвижения = новый ДатаВремя(Движение["Период"] как Строка)', 4)
    s += line('пер До = ВремяДвижения < ВремяГруппы', 4)
    s += line('пер Свое = Ложь', 4)
    s += line('если ВремяДвижения == ВремяГруппы', 4)
    s += line('если Граница != 0', 5) + line('До = Граница == 1', 6) + line('иначе', 5)
    if by_registrar:
        s += line('знч Р = Движение["_RegistrarOrder"] как Строка', 6) + line('знч Г = Снимок["_RegistrarOrder"] как Строка', 6)
        s += line('До = Р < Г', 6) + line('Свое = Р == Г', 6)
    else:
        s += line('Свое = Истина', 6)
    if by_record:
        s += line('если Свое', 6) + line('До = (Движение["Индекс"] как Число) < (Снимок["Индекс"] как Число)', 7)
        s += line('Свое = Движение["Индекс"] == Снимок["Индекс"]', 7) + line(';', 6)
    s += line(';', 5) + line(';', 4)
    for resource in q.resources:
        value = '(Движение['+lit(resource.name)+'] как Число)'
        incoming = '(Движение["ВидЗаписи"] как Строка) == "Приход"'
        delta = '('+incoming+' ? '+value+' : -'+value+')'
        def add(suffix, expression):
            name = lit(resource.name+suffix)
            return line('Снимок['+name+'] = (Снимок['+name+'] как Число) + '+expression, 5)
        s += line('если До', 4) + add('НачальныйОстаток', delta) + add('КонечныйОстаток', delta) + line('иначе если Свое', 4)
        s += add('КонечныйОстаток', delta) + add('Оборот', delta) + add('Приход', '('+incoming+' ? '+value+' : 0)') + add('Расход', '('+incoming+' ? 0 : '+value+')') + line(';', 4)
    s += line(';', 3)
    for resource in q.resources:
        for base in ('НачальныйОстаток', 'КонечныйОстаток'):
            v = '(Снимок['+lit(resource.name+base)+'] как Число)'
            s += line('Снимок.Вставить('+lit(resource.name+base+'Положительный')+', '+v+' > 0 ? '+v+' : 0)', 3)
            s += line('Снимок.Вставить('+lit(resource.name+base+'Отрицательный')+', '+v+' < 0 ? -'+v+' : 0)', 3)
    projected = '{'+', '.join(lit(f.name)+': Снимок['+lit(f.name)+']' for f in required.values())+'}'
    s += line('знч Проекция = СериализацияJson.ЗаписатьОбъект('+projected+')', 3)
    s += line('знч СтрокаДанных = СериализацияJson.ПрочитатьОбъект<Данные>(Проекция, Тип<Данные>)', 3)
    s += line('Все.Добавить(СтрокаДанных)', 3) + line(';')
    return s
