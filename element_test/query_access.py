"""Explicit executor query-read policy; independent of native Element ACL/RLS."""
from .yaml_io import InvalidTestError,UnsupportedSyntaxError
from .resolution import qualified


def prepare_query_access(plan,c):
    policy=plan.check.get('queryAccess')
    if policy is None:return
    if not isinstance(policy,dict) or set(policy)!={'mode','sources'} or policy['mode']!='executor-fixture' or not isinstance(policy['sources'],dict):
        raise InvalidTestError('queryAccess требует mode: executor-fixture и sources')
    prepared={}
    for owner,value in policy['sources'].items():
        matches=c.resolve(owner)
        if len(matches)!=1 or matches[0]['elementType'] not in {'Справочник','Документ'} or qualified(matches[0])!=owner:
            raise InvalidTestError('queryAccess требует точного владельца справочника/документа: '+str(owner))
        if not isinstance(value,dict) or set(value)!={'read','ids'} or not isinstance(value['read'],bool) or not isinstance(value['ids'],list):
            raise InvalidTestError('queryAccess source требует read: Булево, ids: список')
        ids=value['ids']
        if len(ids)>1000 or any(not isinstance(i,str) for i in ids) or len(set(ids))!=len(ids):raise InvalidTestError('queryAccess ids: уникальные строковые ID')
        id_type=plan.check.get('storage',{}).get('idType','Строка')
        if id_type=='Ууид':
            from uuid import UUID
            try:
                for i in ids:
                    if str(UUID(i))!=i:raise ValueError(i)
            except ValueError as e:raise InvalidTestError('queryAccess: некорректный UUID') from e
        for i in ids:c.literal(i,id_type)
        prepared[owner]=value
    c.query_access=prepared
    c.definitions['ТестДоступ']='''@Глобально
метод Проверить(Чтение: Булево)
    если не Чтение
        выбросить новый ИсключениеНедопустимоеСостояние("executor-fixture: source read denied")
    ;
;
'''


def access_guard(q,c):
    policy=getattr(c,'query_access',None)
    if policy is None:return ''
    if q.owner not in policy or not policy[q.owner]['read']:
        return '        ТестДоступ.Проверить(Ложь)\n'
    if q.source_kind!='ordinary':raise UnsupportedSyntaxError('queryAccess: только обычный источник; составной источник требует отдельную политику')
    return ''


def row_guard(q,c):
    policy=getattr(c,'query_access',None)
    if policy is None or q.owner not in policy or not policy[q.owner]['read']:return ''
    ids=policy[q.owner]['ids']
    checks=['ИдДоступа == '+c.literal(i,'Строка') for i in ids]
    return ('                знч СсылкаДоступа = Снимок["Ссылка"] как Соответствие<Строка, Объект?>\n'
            '                знч ИдДоступа = СсылкаДоступа["Идентификатор"] как Строка\n'
            '                если не ('+(' или '.join(checks) or 'Ложь')+')\n                    продолжить\n                ;\n')
