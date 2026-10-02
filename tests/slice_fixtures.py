"""Independent teacher histories and expectations; no query/source evaluation."""
import copy
from pathlib import Path
from record_set_fixtures import audit, REFS, IDS

REPO=Path(__file__).resolve().parent.parent
CORPUS=REPO/'tests/corpus/information-register-slices'
EVIDENCE=REPO/'result/storage-backed-information-register-slices'
ARCHIVE=REPO/'Prakticheskie-primery-2026-09-30-15-20.xdump'

def schema(renamed=False):
    return {'project':'renamed' if renamed else 'prices','namespace':'Вход' if renamed else 'Entry',
            'module':'Поток' if renamed else 'Flow','owner':'Учет::История' if renamed else 'Data::Prices',
            'amount':'Сумма' if renamed else 'Price'}

def seed(s, product, vendor, entries, zone='EU'):
    filt={'Product':product,'Vendor':vendor,'Zone':zone}
    return {'type':s['owner'],'filter':filt,'rows':[
        {'Период':date,**copy.deepcopy(filt),s['amount']:amount,'Factor':factor,'Link':REFS[0],'Note':None}
        for date,amount,factor in entries]}

def seeds(s):
    return [seed(s,REFS[0],REFS[0],[('2026-09-28',100,1),('2026-09-30',5,2),('9999-12-31',999,3)]),
            seed(s,REFS[0],REFS[1],[('2026-09-27',70,4),('2026-09-29',8,5)]),
            seed(s,REFS[1],REFS[0],[('2026-09-28',45,6)]),
            seed(s,None,None,[('2026-09-26',2,7)]),
            seed(s,REFS[0],None,[('2026-09-25',3,8)])]

def check(s,method='Find',args=None,initial=None,sql=False):
    c={'target':{'module':s['module'],'namespace':s['namespace'],'method':method},
       'args':[REFS[0],'2026-09-30'] if args is None else args,
       'storage':{'idType':'Ууид','registers':[s['owner']],
                  'initialRegisters':copy.deepcopy(seeds(s) if initial is None else initial)},'timeout':'15s','trace':True}
    if sql:
        c['storage']['backend']='postgres';c['integration']={'backend':'postgres','operation':'metadata-storage'}
    return c

def expected(c,result):
    return {'result':result,'storage':[audit(s) for s in c['storage']['initialRegisters']]}

def last_rows(s):
    # Fixed independent oracle; indices describe the authored teacher history.
    values=seeds(s)
    return [values[4]['rows'][0],values[3]['rows'][0],values[2]['rows'][0],values[1]['rows'][1],values[0]['rows'][1]]

def expected_reuse(s,c):
    amount=s['amount']
    old=[{'Product':copy.deepcopy(REFS[0]),'Link':copy.deepcopy(REFS[0]),amount:n} for n in (5,8,3)]
    old[0]['Product']=copy.deepcopy(REFS[1])
    new=[{'Product':REFS[0],'Link':None,amount:17},
         {'Product':REFS[0],'Link':REFS[0],amount:5},{'Product':REFS[0],'Link':REFS[0],amount:8}]
    exp=expected(c,{'old':old,'new':new,'again':copy.deepcopy(new),'calls':['date','product']})
    changed=seed(s,REFS[0],None,[('2026-09-30',17,9)])
    changed['rows'][0]['Link']=None;changed['rows'][0]['Note']='new'
    exp['storage'][4]=audit(changed)
    return exp

def real_check(missing=False,empty=False,sql=False):
    owner='РегистрСведений::ЦеныНоменклатурыПоставщиков'
    data=[]
    for p,v,entries in [(REFS[0],REFS[0],[('2026-09-28',100),('2026-09-30',5),('9999-12-31',999)]),
                         (REFS[0],REFS[1],[('2026-09-29',8)]),(REFS[1],REFS[0],[('2026-09-28',45)])]:
        data.append({'type':owner,'filter':{'Номенклатура':p,'Поставщик':v},'rows':[] if empty else [
            {'Период':d,'Номенклатура':p,'Поставщик':v,'Цена':n,'ДокументПоставки':None} for d,n in entries]})
    c={'target':{'module':'ЦеныНоменклатурыПоставщиков','namespace':'РегистрСведений','method':'ПолучитьПоследнююЦенуЗакупки'},
       'args':[REFS[2] if missing else REFS[0]],'storage':{'idType':'Ууид','registers':[owner],'initialRegisters':data},'timeout':'15s','trace':True}
    if sql:c['storage']['backend']='postgres';c['integration']={'backend':'postgres','operation':'metadata-storage'}
    return c

def write_assignments():
    import yaml
    class NoAliases(yaml.SafeDumper):
        def ignore_aliases(self,data):return True
    def write(name,cases):
        folder=REPO/'assignments'/name;folder.mkdir(exist_ok=True)
        (folder/'assignment.yaml').write_text(yaml.dump({'name':'Срез последних — '+name,'checks':[
            {'id':label,'type':'runtime','points':1,'comparison':'record-sets-unordered',**c,'expected':expected(c,result)}
            for label,c,result in cases]},Dumper=NoAliases,allow_unicode=True,sort_keys=False))
    for renamed in (False,True):
        s=schema(renamed)
        for sql in (False,True):
            write('slices-'+s['project']+('-sql' if sql else ''),[
                ('latest',check(s,sql=sql),5),('boundary',check(s,args=[REFS[0],'2026-09-29'],sql=sql),8),
                ('post-slice-filter',check(s,'Resource',['2026-09-30',100],sql=sql),[])])
    for sql in (False,True):
        write('slices-real'+('-sql' if sql else ''),[(label,real_check(missing,empty,sql),result)
            for label,missing,empty,result in [('latest',False,False,5),('missing',True,False,None),('empty',False,True,None)]])
    s=schema()
    write('slices-control', [('wrong-answer',check(s),123),('unsupported',check(s,'Unsupported',[]),None),('recovery',check(s),5)])

if __name__=='__main__':write_assignments()
