"""Independent task 40–42 completion inputs, fixed before source execution."""
from pathlib import Path
import shutil
from yaml import safe_load, safe_dump
from projection_fixtures import REPO, portable_check, expected, REFS

OUT=REPO/'result/query-040-042-completion'
CORPUS=REPO/'tests/corpus/query-040-042-completion'
FORMS=REPO/'tests/corpus/storage-query-fill/documented-forms'
JOIN_ANSWERS={
 'left-join':[{'Label':x} for x in 'abcd'],
 'inner-join':[{'Label':x} for x in 'ab'],
 'right-join':[{'Label':x} for x in 'ab']+[{'Label':None}],
 'full-join':[{'Label':x} for x in 'abcd']+[{'Label':None}],
 'nullable':[],
 'replace-null':[{'Value':'present'},{'Value':None},{'Value':''},{'Value':None}],
}
QUERIES={
 'Math':'ВЫБРАТЬ ACos(-1) КАК A, ASin(1) КАК B, ATan(1) КАК C, Cos(15) КАК D, Sin(15) КАК E, Tan(0.8) КАК F, Exp(5) КАК G, Log(10) КАК H, Log10(100) КАК I, Степень(2,6) КАК J, 2.1 ** 3 КАК K, Корень(64) КАК L, 2 ** -10 КАК M ИЗ Other::Item',
 'First':'ВЫБРАТЬ A.Key, ПервыйНеNull(B.Amount, 9, 10 / 0) КАК Value ИЗ Data::Item КАК A ЛЕВОЕ СОЕДИНЕНИЕ Other::Item КАК B ПО A.Key == B.Key УПОРЯДОЧИТЬ ПО A.Amount',
 'AllNull':'ВЫБРАТЬ ПервыйНеNull(NULL, NULL) КАК Value ИЗ Other::Item',
 'OptionalValue':'ВЫБРАТЬ ПервыйНеNull(Peer, NULL) КАК Value ИЗ Data::Item УПОРЯДОЧИТЬ ПО Amount',
 'Match':'ВЫБРАТЬ Label, Label.ПолноеСовпадение(%Pattern) КАК Match ИЗ Data::Item УПОРЯДОЧИТЬ ПО Label',
 'DateParts':'ВЫБРАТЬ Day.Год КАК Year, Day.Месяц КАК Month, Day.День КАК Day, КОЛИЧЕСТВО(*) КАК Count ИЗ Data::Item СГРУППИРОВАТЬ ПО Day.Год, Day.Месяц, Day.День',
 'TempFields':'''СОЗДАТЬ ВРЕМЕННУЮ ТАБЛИЦУ T (Id АвтоНомерЗаписи, Qty: Число(3, 1), Price: Число(3, 2) ПО УМОЛЧАНИЮ 2.555, Amount: Число(5, 2) ПО УМОЛЧАНИЮ Qty * Price, Calc ВЫЧИСЛЯЕТСЯ КАК Qty * Price, Label: Строка(3) ПО УМОЛЧАНИЮ "abcdef", Optional: Строка?);
 ВСТАВИТЬ В T (Qty) ЗНАЧЕНИЯ (2.34);
 ВСТАВИТЬ В T (Qty, Amount, Label) ЗНАЧЕНИЯ (1, 99.995, "xy");
 ИЗМЕНИТЬ T УСТАНОВИТЬ Qty = 4.26 ГДЕ Id == 1;
 СОЗДАТЬ ИНДЕКС I ДЛЯ T (Id) ДОПОЛНИТЕЛЬНО ПО (Amount);
 ВЫБРАТЬ Id, Qty, Price, Amount, Calc, Label, Optional ИЗ T УПОРЯДОЧИТЬ ПО Id''',
 'TempSelect':'''СОЗДАТЬ ВРЕМЕННУЮ ТАБЛИЦУ T (Id АвтоНомерЗаписи, Qty: Число(3, 1), Price: Число(3, 2) ПО УМОЛЧАНИЮ 2.555, Amount: Число(5, 2) ПО УМОЛЧАНИЮ Qty * Price);
 ВСТАВИТЬ В T (Qty) ВЫБРАТЬ Amount ИЗ Data::Item УПОРЯДОЧИТЬ ПО Label;
 ВЫБРАТЬ Id, Qty, Amount ИЗ T УПОРЯДОЧИТЬ ПО Id''',
 'TempCapture':'''СОЗДАТЬ ВРЕМЕННУЮ ТАБЛИЦУ T (Qty: Число, Price: Число ПО УМОЛЧАНИЮ 2, Amount: Число ПО УМОЛЧАНИЮ Qty * Price);
 ВСТАВИТЬ В T (Qty) ЗНАЧЕНИЯ (%{Capture(Calls)});
 ВЫБРАТЬ Qty, Amount ИЗ T''',
 'TempLife':'''СОЗДАТЬ ВРЕМЕННУЮ ТАБЛИЦУ T (Id АвтоНомерЗаписи, Value: Строка(0));
 ВСТАВИТЬ В T (Value) ЗНАЧЕНИЯ ("one");
 УДАЛИТЬ ИЗ T;
 ВСТАВИТЬ В T (Value) ЗНАЧЕНИЯ ("two");
 ВЫБРАТЬ Id, Value ИЗ T''',
 'NullDefaults':'''СОЗДАТЬ ВРЕМЕННУЮ ТАБЛИЦУ T (Id АвтоНомерЗаписи, Value: Строка? ПО УМОЛЧАНИЮ "fallback");
 ВСТАВИТЬ В T (Value) ЗНАЧЕНИЯ (NULL);
 ВСТАВИТЬ В T (Value) ЗНАЧЕНИЯ (Неопределено);
 ВЫБРАТЬ Id, Value, Value ЕСТЬ NULL КАК Missing ИЗ T УПОРЯДОЧИТЬ ПО Id''',
 'Unsupported':'ВЫБРАТЬ Key ИЗ Data::Item ОБЪЕДИНИТЬ ВЫБРАТЬ Label ИЗ Other::Item',
}
ANSWERS={
 'Math':[{'A':3.141592654,'B':1.570796327,'C':0.785398163,'D':-0.759687913,'E':0.65028784,'F':1.029638557,'G':148.413159103,'H':2.302585093,'I':2,'J':64,'K':9.261,'L':8,'M':0.000977}],
 'First':[{'Key':3,'Value':9},{'Key':1,'Value':2},{'Key':1,'Value':2},{'Key':2,'Value':9}],
 'AllNull':[{'Value':None}],
 'OptionalValue':[{'Value':None},{'Value':REFS[0]},{'Value':REFS[0]},{'Value':None}],
 'Match':[{'Label':'a','Match':True},{'Label':'b','Match':True},{'Label':'c','Match':False},{'Label':'d','Match':False}],
 'DateParts':[{'Year':2024,'Month':2,'Day':28,'Count':4}],
 'TempFields':[{'Id':1,'Qty':4.3,'Price':2.56,'Amount':5.89,'Calc':11.008,'Label':'abc','Optional':None},{'Id':2,'Qty':1,'Price':2.56,'Amount':100,'Calc':2.56,'Label':'xy','Optional':None}],
 'TempSelect':[{'Id':1,'Qty':1,'Amount':2.56},{'Id':2,'Qty':3,'Amount':7.68},{'Id':3,'Qty':3,'Amount':7.68},{'Id':4,'Qty':0,'Amount':0}],
 'TempCapture':{'first':[{'Qty':3,'Amount':6}],'again':[{'Qty':3,'Amount':6}],'calls':1},
 'TempLife':{'first':[{'Id':2,'Value':'two'}],'again':[{'Id':2,'Value':'two'}]},
 'NullDefaults':[{'Id':1,'Value':None,'Missing':True},{'Id':2,'Value':None,'Missing':False}],
 'Uuid':{'count':4,'distinct':True,'fresh':True},
}


def config(method,variant='ordinary',sql=False,empty=False):
    c=portable_check(variant,method,sql,empty)
    for seed in c['storage']['initial']:
        if seed['type'].startswith(('Data::','Учет::')):seed['value']['Optional']=None
    c['args']=['[ab]'] if method=='Match' else []
    if method.startswith('join_'):
        for i,seed in enumerate(c['storage']['initial']):
            if seed['type'].startswith(('Data::','Учет::')):seed['value']['Optional']=['present',None,'',None][i]
        c['storage']['initial'].append({'type':'Other::Item' if variant=='ordinary' else 'Other::Строки','value':{'Ссылка':REFS[5],'Key' if variant=='ordinary' else 'Номер':99,'Amount' if variant=='ordinary' else 'Значение':8,'Label':'unmatched','Flag':False,'Day':'2024-02-28','Peer':None,'Kind':'One'}})
    return c


def build():
    for variant in ('ordinary','renamed'):
        root=CORPUS/variant
        shutil.copytree(REPO/'tests/corpus/projections-aggregates'/variant,root,dirs_exist_ok=True)
        ns,item,main=('Data','Item','Main') if variant=='ordinary' else ('Учет','Строки','Пуск')
        p=root/ns/(item+'.yaml');meta=safe_load(p.read_text())
        if not any(f['Имя']=='Optional' for f in meta['Реквизиты']):meta['Реквизиты'].append({'Имя':'Optional','Тип':'Строка?'})
        p.write_text(safe_dump(meta,allow_unicode=True,sort_keys=False))
        code=[]
        for name,q in QUERIES.items():
            if variant=='renamed':q=q.replace('Data::Item','Учет::Строки').replace('Other::Item','Other::Строки').replace('Amount ИЗ Data','Значение ИЗ Data')
            # Rename source attributes only, retaining temporary schema names.
            if variant=='renamed':q=q.replace('A.Key','A.Номер').replace('B.Key','B.Номер').replace('A.Amount','A.Значение').replace('B.Amount','B.Значение').replace('ВЫБРАТЬ A.Номер,','ВЫБРАТЬ A.Номер КАК Key,').replace('ВЫБРАТЬ Amount ИЗ Учет','ВЫБРАТЬ Значение ИЗ Учет').replace('ВЫБРАТЬ Key ИЗ Учет','ВЫБРАТЬ Номер ИЗ Учет')
            if variant=='renamed':q=q.replace('УПОРЯДОЧИТЬ ПО Amount','УПОРЯДОЧИТЬ ПО Значение')
            args='(Pattern: Строка)' if name=='Match' else '()'
            body='    возврат Запрос{'+q+'}.Выполнить()'
            if name in ('TempCapture','TempLife'):
                body=('    знч Calls = новый Массив<Число>()\n' if name=='TempCapture' else '')+'    знч Q = Запрос{'+q+'}\n    знч First = Q.Выполнить()\n    возврат {"first": First, "again": Q.Выполнить()'+(', "calls": Calls.Размер()' if name=='TempCapture' else '')+'}'
            code.append('метод '+name+args+': Объект\n'+body+'\n;\n')
        for label in JOIN_ANSWERS:
            q=(FORMS/(label+'.xbql')).read_text().strip()
            if variant=='renamed':q=q.replace('Data::Item','Учет::Строки').replace('Other::Item','Other::Строки').replace('.Key','.Номер')
            code.append('метод join_'+label.replace('-','_')+'(): Объект\n    возврат Запрос{'+q+'}.Выполнить()\n;\n')
        code.append('''метод Capture(Calls: Массив<Число>): Число
    Calls.Добавить(3)
    возврат 3
;
метод Uuid(): Объект
    знч Q = Запрос{ВЫБРАТЬ Ууид() КАК Id ИЗ '''+ns+'::'+item+'''}
    знч First = Q.Выполнить()
    знч Again = Q.Выполнить()
    знч Unique = новый Массив<Ууид>()
    пер Fresh = Истина
    для R из First
        если не Unique.Содержит(R.Id)
            Unique.Добавить(R.Id)
        ;
        для S из Again
            если R.Id == S.Id
                Fresh = Ложь
            ;
        ;
    ;
    возврат {"count": First.Размер(), "distinct": Unique.Размер() == First.Размер(), "fresh": Fresh}
;
''')
        (root/'Entry'/(main+'.xbsl')).write_text(''.join(code))
    OUT.mkdir(parents=True,exist_ok=True)

if __name__=='__main__':build()
