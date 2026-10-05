"""Column names and value-type sets derived from the query AST, never rows."""
TYPE = 'ТестТипДанныхЗапроса.Описание'
COLUMN = 'ТестКолонкаЗапроса.Описание'
NUMBER = 'ТестКвалификаторЧисла.Значение'
STRING = 'ТестКвалификаторСтроки.Значение'
BYTES = 'ТестКвалификаторБайт.Значение'


def bind_platform_type(c, spelling, canonical):
    # An explicit Std name identifies the platform. A project/local declaration
    # owns the unqualified spelling and must never be replaced by a fake.
    shadowed=any(e['name']==spelling for e in c.model['elements']) or any(
        spelling in declarations for declarations in c.local_by_source.values())
    if not shadowed:c.platform_type_aliases[spelling]=canonical
    c.platform_type_aliases['Стд::БазаДанных::'+spelling]=canonical


def prepare_columns(c):
    for typ, fields in [(NUMBER,('ДлинаЦелойЧасти','ДлинаДробнойЧасти')),
                        (STRING,('ДлинаСтроки',)),(BYTES,('ДлинаБайт',))]:
        c.definitions.setdefault(typ,'@Глобально\nструктура Значение\n'+''.join(
            '    знч '+field+': Число\n' for field in fields)+';\n')
        spelling={'ТестКвалификаторЧисла':'КвалификаторЧисла',
                  'ТестКвалификаторСтроки':'КвалификаторСтроки',
                  'ТестКвалификаторБайт':'КвалификаторБайт'}[typ.split('.')[0]]
        bind_platform_type(c,spelling,typ)
    c.definitions.setdefault(TYPE, '''@Глобально
структура Описание
    обз знч Типы: ЧитаемоеМножество<Тип>
    знч КвалификаторЧисла: ТестКвалификаторЧисла.Значение?
    знч КвалификаторСтроки: ТестКвалификаторСтроки.Значение?
    знч КвалификаторБайт: ТестКвалификаторБайт.Значение?
;
''')
    c.definitions.setdefault(COLUMN, '''@Глобально
структура Описание
    знч Имя: Строка
    обз знч ТестТип: ТестТипДанныхЗапроса.Описание
;
''')
    c.method_dependencies[COLUMN] = [TYPE]
    c.method_dependencies[TYPE] = [NUMBER,STRING,BYTES]
    for spelling, canonical in [('ТипДанныхБазыДанных',TYPE),('ОписаниеКолонкиРезультатаЗапроса',COLUMN)]:
        bind_platform_type(c,spelling,canonical)


def columns_source(query, c, indent='        '):
    prepare_columns(c)
    lines = [indent+'знч Колонки = новый Массив<'+COLUMN+'>()']
    for index,(field,label) in enumerate(query.projections):
        base = field.type.rstrip('?')
        c.require(base)
        variable = 'ТестТипы'+str(index)
        lines += [indent+'знч '+variable+' = новый Множество<Тип>()',
                  indent+variable+'.Добавить(Тип<'+c.sbsl_type(base)+'>)']
        if field.type.endswith('?') or getattr(field,'sql_nullable',False):
            lines.append(indent+variable+'.Добавить(Тип<Неопределено>)')
        qualifiers = qualifier_source(field)
        lines.append(indent+'Колонки.Добавить(новый '+COLUMN+'(Имя = '+c.literal(label,'Строка')+
                     ', ТестТип = новый '+TYPE+'(Типы = '+variable+qualifiers+')))')
    return '\n'.join(lines)+'\n'


def qualifier_source(field):
    """Explicit fake defaults, plus exact projection cast qualifiers from AST."""
    typ = field.type.rstrip('?')
    values = field.value[1] if getattr(field,'kind',None)=='cast' else ()
    if typ=='Строка':
        length=values[0] if values else 0
        return ', КвалификаторСтроки = новый '+STRING+'(ДлинаСтроки = '+str(length)+')'
    if typ=='Число':
        integer=values[0] if values else 32
        fraction=values[1] if len(values)>1 else 0 if values else 32
        return ', КвалификаторЧисла = новый '+NUMBER+'(ДлинаЦелойЧасти = '+str(integer)+', ДлинаДробнойЧасти = '+str(fraction)+')'
    if typ=='Байты':
        return ', КвалификаторБайт = новый '+BYTES+'(ДлинаБайт = 0)'
    return ''


def lower_columns(plan, symbol, results):
    """Script reserves Тип; rename only a proven column descriptor property."""
    import re
    from .indexer import IDENT, parse_module
    source = symbol.source
    node = parse_module(source)[0][0]
    arrays, columns = set(), set()
    from .indexer import mask_noncode
    code = mask_noncode(source)
    for p in node.parameters(source):
        name,_,typ=p.partition(':')
        if re.fullmatch(r'(?:Стд::БазаДанных::)?ОписаниеКолонкиРезультатаЗапроса',typ.strip()) and plan.contracts.platform_type_aliases.get(typ.strip())==COLUMN:
            columns.add(name.strip())
        if re.fullmatch(r'ЧитаемыйМассив<(?:Стд::БазаДанных::)?ОписаниеКолонкиРезультатаЗапроса>',typ.strip()) and plan.contracts.platform_type_aliases.get(typ.strip().partition('<')[2][:-1])==COLUMN:
            arrays.add(name.strip())
    def array(e):
        if e.kind=='name':return e.value in arrays
        m=e.children[0] if e.kind=='call' and len(e.children)==1 else None
        return bool(m and m.kind=='member' and m.children[0].value in results
                    and m.children[1].value=='ПолучитьОписанияКолонок')
    def column(e):
        return (e.kind=='name' and e.value in columns or
                e.kind=='index' and array(e.children[0]))
    spans=plan.source_transforms.setdefault((symbol.identity.source_file,symbol.identity.declaration),[])
    for item in node.expression_tree.walk():
        if item.kind=='statement' and item.value in {'знч','пер'} and item.children:
            m=re.match(rf'(?:знч|пер)\s+({IDENT})\s*=',source[item.start:item.end])
            if m:
                if array(item.children[-1]):arrays.add(m[1])
                if column(item.children[-1]):columns.add(m[1])
        if item.kind=='statement' and item.value=='для' and item.children and array(item.children[0]):
            m=re.match(rf'для\s+({IDENT})\s+из',source[item.start:item.end])
            if m:columns.add(m[1])
        if item.kind=='member' and item.children[1].value=='Тип' and column(item.children[0]):
            e=item.children[1]
            spans.append((e.start,e.end,'ТестТип'))
    for name in arrays|columns:
        assignments = list(re.finditer(rf'\b{re.escape(name)}\s*=(?!=)',code))
        declarations = re.findall(rf'\b(?:знч|пер)\s+{re.escape(name)}\s*(?::[^=\n]+)?=',code)
        if len(assignments)>1 or len(declarations)>1:
            raise UnsupportedSyntaxError('Описание колонки требует незатенённого неизменённого получателя: '+name)
