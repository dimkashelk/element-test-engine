"""Runtime XBQL subset emulator; parsing and business rows execute in Script.

Python emits schema loaders through the ordinary query AST/renderer. No argument,
fixture or expected value selects a plan. Unlike finite text specialization this
path accepts text from parameters and permits different result column schemas.
"""
from hashlib import sha256
from pathlib import Path

from .query_columns import COLUMN, columns_source
from .query_results import result_type
from .query_sources import source_schema
from .resolution import qualified

ENGINE = 'ТестРазборТекста'
ROW = 'Соответствие<Строка, Объект?>'


def generate_runtime_text(plan, c):
    from .storage_queries import generate_query
    from .query_plan import parse_storage_query
    from .resolution import resolve_symbols, visible_from
    from .generated_types import SCALARS
    result = result_type(ROW,c)
    c.definitions.setdefault(ENGINE,Path(__file__).with_name('query_text.sbsl').read_text())
    c.method_dependencies[ENGINE] = [COLUMN]
    schema_sources = []
    for element in c.model['elements']:
        if element['elementType'] not in {'Справочник','Документ'} or not visible_from(element,c.namespace):
            continue
        identity = qualified(element)
        schema = source_schema(c,identity)
        # Rich member/virtual/join queries continue to use the common static AST.
        # Runtime text exposes only scalar/reference columns of ordinary sources.
        fields = [f for f in schema['fields'] if f.type.rstrip('?') in SCALARS
                  or f.type.rstrip('?').endswith('.Ссылка')
                  or any(e['elementType']=='Перечисление' for e in c.resolve(f.type.rstrip('?')))]
        if fields:
            q = parse_storage_query('ВЫБРАТЬ '+', '.join(f.name for f in fields)+' ИЗ '+identity,c)
            schema_sources.append((identity,element,q,generate_query(q,c)))
    for key,schema in getattr(c,'custom_query_sources',{}).items():
        q = parse_storage_query('ВЫБРАТЬ '+', '.join(f.name for f in schema['fields'])+' ИЗ '+key,c)
        schema_sources.append((schema['owner'],None,q,generate_query(q,c)))
    namespace, imports = c.namespace,c.imports
    identity = (namespace,tuple(imports),tuple((owner,q.to_dict()) for owner,_,q,_ in schema_sources))
    name = 'ТестRuntimeЗапрос'+sha256(str(identity).encode()).hexdigest()[:16]
    c.dynamic_row_modules=getattr(c,'dynamic_row_modules',{})
    c.dynamic_row_modules[name]=ENGINE
    c.dynamic_row_types=getattr(c,'dynamic_row_types',{})
    c.dynamic_row_types[name]=ROW
    c.runtime_query_sources=[owner for owner,_,_,_ in schema_sources]
    if name in c.definitions:
        return name
    branches=[]; dependencies=[ENGINE+'.Дерево',result,COLUMN]
    for owner,element,q,base in schema_sources:
        if element and element not in plan.storage_elements:
            plan.storage_elements.append(element)
        aliases=[owner]
        if element:
            # Only names that resolve to this owner in the caller are accepted.
            for candidate in [element['name']]:
                matches = resolve_symbols([e for e in c.model['elements'] if visible_from(e,namespace)],candidate,namespace,imports,c.model.get('properties'))
                if matches == [element]: aliases.append(candidate)
        condition=' или '.join('Дерево.Источник == '+c.literal(alias,'Строка') for alias in aliases)
        dependencies.append(base+'.Запрос')
        args = ''
        if not element:
            key=owner.removeprefix('&')
            schema=c.custom_query_sources['_'+key]
            args='ЧитатьИсточник('+c.literal(key,'Строка')+') как '+c.sbsl_type(schema['type'])
        projection='{'+', '.join(c.literal(label,'Строка')+': С.'+label for _,label in q.projections)+'}'
        branches.append('        если '+condition+'\n'+columns_source(q,c,'            ')+
            '            знч Исходные = новый Массив<'+ROW+'>()\n'+
            '            для С из '+base+'.Создать('+args+').Выполнить()\n'+
            '                Исходные.Добавить('+projection+')\n            ;\n'+
            '            знч Строки = '+ENGINE+'.Выполнить(Дерево, Исходные, Колонки)\n'+
            '            возврат '+result.removesuffix('.Результат')+'.Создать(Строки, (Значение: Объект?) -> Значение как '+ROW+', '+ENGINE+'.Колонки(Дерево, Колонки))\n        ;\n')
    # Use the same setters and dictionaries as the finite specialization.
    # Both paths use the platform setter contract; parsing is runtime here.
    text='''@Глобально
структура Запрос
    пер Текст: Строка
    знч Параметры: Соответствие<Строка, Объект?>
    знч Источники: Соответствие<Строка, Объект?>
    @Глобально
    метод УстановитьПараметр(Имя: Строка, Значение: Объект?)
        Параметры[Имя] = Значение
    ;
    @Глобально
    метод УстановитьИсточникДанных(Имя: Строка, Значение: Объект?)
        Источники[Имя] = Значение
    ;
    метод ЧитатьИсточник(Имя: Строка): Объект?
        если не Источники.СодержитКлюч(Имя)
            выбросить новый ИсключениеНедопустимоеСостояние("Не установлен источник запроса: " + Имя)
        ;
        возврат Источники[Имя]
    ;
    @Глобально
    метод Выполнить(): RESULT
        знч Дерево = новый ТестРазборТекста.Разбор(Токены = ТестРазборТекста.Лексер(Текст), Параметры = Параметры).Прочитать()
BRANCHES
        выбросить новый ИсключениеНедопустимыйАргумент("Неизвестный источник runtime-запроса: " + Дерево.Источник)
    ;
;
@Глобально
метод Создать(Текст: Строка): Запрос
    возврат новый Запрос(Текст = Текст, Параметры = новый Соответствие<Строка, Объект?>(), Источники = новый Соответствие<Строка, Объект?>())
;
'''.replace('RESULT',result).replace('BRANCHES',''.join(branches))
    c.definitions[name]=text
    c.method_dependencies[name]=dependencies
    return name
