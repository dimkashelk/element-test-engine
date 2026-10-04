"""Explicit identity-only current-user fixture for standalone query captures.

This does not implement authentication, user lookup, rights or platform APIs.
"""
from .yaml_io import InvalidTestError, UnsupportedSyntaxError

OWNER='ТестТекущийПользователь'

def prepare_query_context(plan,contracts):
    context=plan.check.get('queryContext')
    if context is None:return
    if not isinstance(context,dict) or set(context)!={'currentUser'}:
        raise InvalidTestError('queryContext требует только currentUser')
    if contracts.resolve('Пользователи'):
        raise UnsupportedSyntaxError('Проектный тип затеняет системный Пользователи')
    typ=OWNER+'.Ссылка'
    contracts.platform_type_aliases['Пользователи.Ссылка']=typ
    contracts.platform_type_aliases['Стд::Пользователи::Пользователи.Ссылка']=typ
    contracts.fields[typ]=[{'Имя':'Идентификатор','Тип':'Ууид','constructorRequired':True}]
    value=contracts.literal(context['currentUser'],typ)
    contracts.definitions[typ]='@Глобально\nструктура Ссылка\n    знч Идентификатор: Ууид\n;\n@Глобально\nметод Текущий(): Ссылка\n    возврат '+value+'\n;\n'
    contracts.query_context_expressions={'Пользователи.ТекущийПользователь':OWNER+'.Текущий()',
         'Стд::Пользователи::Пользователи.ТекущийПользователь':OWNER+'.Текущий()'}
