"""Explicit identity-only current-user fixture for standalone query captures.

This does not implement authentication, user lookup, rights or platform APIs.
"""
from .yaml_io import InvalidTestError, UnsupportedSyntaxError

OWNER='ТестТекущийПользователь'

def prepare_query_context(plan,contracts):
    context=plan.check.get('queryContext')
    if context is None:return
    if not isinstance(context,dict) or not context or set(context)-{'currentUser','users'}:
        raise InvalidTestError('queryContext требует currentUser и/или users')
    if contracts.resolve('Пользователи'):
        raise UnsupportedSyntaxError('Проектный тип затеняет системный Пользователи')
    typ=OWNER+'.Ссылка'
    contracts.platform_type_aliases['Пользователи.Ссылка']=typ
    contracts.platform_type_aliases['Стд::Пользователи::Пользователи.Ссылка']=typ
    contracts.fields[typ]=[{'Имя':'Идентификатор','Тип':'Ууид','constructorRequired':True}]
    contracts.definitions[typ]='@Глобально\nструктура Ссылка\n    знч Идентификатор: Ууид\n;\n'
    if 'currentUser' in context:
        value=contracts.literal(context['currentUser'],typ)
        contracts.definitions[typ]+='@Глобально\nметод Текущий(): Ссылка\n    возврат '+value+'\n;\n'
    if 'users' in context:
        from .form_context import add_structure
        rows=context['users']
        fields=[{'Имя':'Ссылка','Тип':typ},{'Имя':'Представление','Тип':'Строка'}]+[{'Имя':n,'Тип':'Булево'} for n in ('Администратор','БылУспешныйВход','ЗапрещенВход','РазрешенДоступПоТокену')]
        if not isinstance(rows,list) or len(rows)>100 or any(not isinstance(r,dict) or set(r)!={f['Имя'] for f in fields} for r in rows):
            raise InvalidTestError('users: до 100 полных записей поддержанного системного профиля')
        keys=[contracts.literal(r['Ссылка'],typ) for r in rows]
        if len(set(keys))!=len(keys):raise InvalidTestError('users: повторная системная ссылка')
        row_type='ТестСистемныеИсточники.Пользователь'
        add_structure(contracts,row_type,fields)
        value=contracts.literal(rows,'Массив<'+row_type+'>')
        contracts.definitions[row_type]+='\n@Глобально\nметод Пользователи(): Массив<'+row_type+'>\n    возврат '+value+'\n;\n'
        contracts.method_dependencies[row_type]=[typ]
        contracts.query_user_fields=fields
    contracts.query_context_expressions=({'Пользователи.ТекущийПользователь':OWNER+'.Текущий()',
         'Стд::Пользователи::Пользователи.ТекущийПользователь':OWNER+'.Текущий()'} if 'currentUser' in context else {})
