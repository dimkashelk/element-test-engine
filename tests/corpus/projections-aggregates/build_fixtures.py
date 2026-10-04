"""Portable projects only; teacher answers live separately in projection_fixtures."""
from pathlib import Path
import yaml
ROOT=Path(__file__).resolve().parent
QUERIES={
 'Computed':'ВЫБРАТЬ Label + "!" КАК Text, Amount * 2 + 0.1 + 0.2 КАК Double, ВЫБОР КОГДА Flag ТОГДА Amount ИНАЧЕ 0 КОНЕЦ КАК Choice, Label.Подстрока(0, 1) КАК Prefix, Day.ДобавитьДни(1) КАК Tomorrow ИЗ Data::Item УПОРЯДОЧИТЬ ПО Text',
 'Aggregate':'ВЫБРАТЬ КОЛИЧЕСТВО(*) КАК Count, СУММА(Amount) КАК Sum, МИНИМУМ(Amount) КАК Min, МАКСИМУМ(Amount) КАК Max, СРЕДНЕЕ(Amount) КАК Avg ИЗ Data::Item',
 'Grouped':'ВЫБРАТЬ Key, КОЛИЧЕСТВО(*) КАК Count, СУММА(Amount) КАК Sum ИЗ Data::Item СГРУППИРОВАТЬ ПО Key УПОРЯДОЧИТЬ ПО Key',
 'Having':'ВЫБРАТЬ ПЕРВЫЕ 1 Key, СУММА(Amount) КАК Sum ИЗ Data::Item ГДЕ Amount > 0 СГРУППИРОВАТЬ ПО Key ИМЕЮЩИЕ СУММА(Amount) >= 4 УПОРЯДОЧИТЬ ПО Sum УБЫВ',
 'Distinct':'ВЫБРАТЬ РАЗЛИЧНЫЕ Key ИЗ Data::Item УПОРЯДОЧИТЬ ПО Key',
 'DistinctAggregate':'ВЫБРАТЬ КОЛИЧЕСТВО(РАЗЛИЧНЫЕ Key) КАК Count, СУММА(РАЗЛИЧНЫЕ Amount) КАК Sum, СРЕДНЕЕ(РАЗЛИЧНЫЕ Amount) КАК Avg ИЗ Data::Item',
 'JoinGroup':'ВЫБРАТЬ A.Key, КОЛИЧЕСТВО(*) КАК Rows, КОЛИЧЕСТВО(B.Amount) КАК Present, СУММА(B.Amount) КАК Sum, СУММА(B.Amount.ЗаменитьNull(0)) КАК Safe ИЗ Data::Item КАК A ЛЕВОЕ СОЕДИНЕНИЕ Other::Item КАК B ПО A.Key == B.Key СГРУППИРОВАТЬ ПО A.Key УПОРЯДОЧИТЬ ПО A.Key',
 'NullGroup':'ВЫБРАТЬ B.Label КАК Name, КОЛИЧЕСТВО(*) КАК Count ИЗ Data::Item КАК A ЛЕВОЕ СОЕДИНЕНИЕ Other::Item КАК B ПО A.Key == B.Key СГРУППИРОВАТЬ ПО B.Label',
 'NullDistinct':'ВЫБРАТЬ РАЗЛИЧНЫЕ B.Label КАК Name ИЗ Data::Item КАК A ЛЕВОЕ СОЕДИНЕНИЕ Other::Item КАК B ПО A.Key == B.Key',
 'Case':'ВЫБРАТЬ Key, ВЫБОР Key КОГДА 1 ТОГДА "one" КОГДА 2 ТОГДА "two" КОНЕЦ КАК Name, ВЫБОР КОГДА Amount == 0 ТОГДА 0 ИНАЧЕ 10 / Amount КОНЕЦ КАК Quotient ИЗ Data::Item',
 'Cast':'ВЫБРАТЬ ВЫРАЗИТЬ(Amount КАК Число(5, 2)) КАК Rounded, ВЫРАЗИТЬ(Label КАК Строка(1)) КАК Text ИЗ Data::Item',
 'Typed':'ВЫБРАТЬ Peer, Kind, КОЛИЧЕСТВО(*) КАК Count ИЗ Data::Item СГРУППИРОВАТЬ ПО Peer, Kind',
 'Fill':'ВЫБРАТЬ Key, СУММА(Amount) КАК Sum ЗАПОЛНИТЬ Data::Card ИЗ Data::Item СГРУППИРОВАТЬ ПО Key',
 'Params':'ВЫБРАТЬ Key, СУММА(Amount + %Bonus) КАК Sum ИЗ Data::Item ГДЕ Key >= %Threshold СГРУППИРОВАТЬ ПО Key ИМЕЮЩИЕ СУММА(Amount + %Bonus) > %Min УПОРЯДОЧИТЬ ПО Sum',
 'GroupedExpression':'ВЫБРАТЬ Key * 2 КАК K, КОЛИЧЕСТВО(*) КАК Count ИЗ Data::Item СГРУППИРОВАТЬ ПО Key * 2',
 'NullUndefined':'ВЫБРАТЬ ВЫБОР КОГДА Key == 1 ТОГДА NULL ИНАЧЕ Peer КОНЕЦ КАК Value, КОЛИЧЕСТВО(*) КАК Count ИЗ Data::Item СГРУППИРОВАТЬ ПО ВЫБОР КОГДА Key == 1 ТОГДА NULL ИНАЧЕ Peer КОНЕЦ',
 'CountUndefined':'ВЫБРАТЬ КОЛИЧЕСТВО(Peer) КАК DefinedRows, КОЛИЧЕСТВО(NULL) КАК NullRows ИЗ Data::Item',
 'Precision':'ВЫБРАТЬ ПЕРВЫЕ 1 ВЫРАЗИТЬ(1.005 КАК Число(1, 2)) КАК Positive, ВЫРАЗИТЬ(-1.005 КАК Число(1, 2)) КАК Negative, 0.1000000000000000000001 + 0.2000000000000000000002 КАК Exact, 5 % 2 КАК Remainder ИЗ Data::Item',
 'DistinctLimit':'ВЫБРАТЬ ПЕРВЫЕ 2 РАЗЛИЧНЫЕ Amount КАК Value ИЗ Data::Item УПОРЯДОЧИТЬ ПО Value УБЫВ',
 'Unsupported':'ВЫБРАТЬ СУММА(Amount) КАК Sum ИЗ Data::Item ОБЪЕДИНИТЬ ВЫБРАТЬ Amount ИЗ Data::Item',
}

def main():
 for variant,ns,item,main,key,amount in [('ordinary','Data','Item','Main','Key','Amount'),('renamed','Учет','Строки','Пуск','Номер','Значение')]:
  root=ROOT/variant
  def write(path,data):
   p=root/path;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(yaml.safe_dump(data,allow_unicode=True,sort_keys=False))
  write(Path('Проект.yaml'),{'Имя':'projection-'+variant,'Поставщик':'Teacher','РежимСовместимости':'9.3'})
  for owner in (ns,'Other'):
   write(Path(owner)/(item+'.yaml'),{'ВидЭлемента':'Справочник','Имя':item,'ОбластьВидимости':'ВПроекте','Реквизиты':[{'Имя':key,'Тип':'Число'},{'Имя':amount,'Тип':'Число'},{'Имя':'Label','Тип':'Строка'},{'Имя':'Flag','Тип':'Булево'},{'Имя':'Day','Тип':'Дата'},{'Имя':'Peer','Тип':ns+'::'+item+'.Ссылка?'},{'Имя':'Kind','Тип':ns+'::Category'}]})
  write(Path(ns)/'Category.yaml',{'ВидЭлемента':'Перечисление','Имя':'Category','ОбластьВидимости':'ВПроекте','Элементы':[{'Имя':'One','ПоУмолчанию':True},{'Имя':'Two'}]})
  write(Path(ns)/'Card.yaml',{'ВидЭлемента':'Структура','Имя':'Card','ОбластьВидимости':'ВПроекте','Поля':[{'Имя':key,'Тип':'Число'},{'Имя':'Sum','Тип':'Число?'}]})
  source=''
  def convert(text):
   import re
   text=text.replace('Data::Item',ns+'::'+item).replace('Other::Item','Other::'+item).replace('Data::Card',ns+'::Card')
   return re.sub(r'\bAmount\b',amount,re.sub(r'\bKey\b',key,text))
  for method,query in QUERIES.items():
   args='Bonus: Число, Threshold: Число, Min: Число' if method=='Params' else ''
   source+='метод '+method+'('+args+'): Объект\n    возврат Запрос{'+convert(query)+'}.Выполнить()\n;\n'
  source+='метод Capture(Calls: Массив<Строка>, N: Число): Число\n    Calls.Добавить("capture")\n    возврат N\n;\n'
  source+='метод State(): Объект\n    знч Calls = новый Массив<Строка>()\n    знч Q = Запрос{'+convert('ВЫБРАТЬ СУММА(Amount + %{Capture(Calls, 1)}) КАК Sum ИЗ Data::Item')+'}\n    знч Old = Q.Выполнить()\n    пер Seen: Объект = новый Массив<Объект>()\n    попытка\n        исп Транзакции.Начать()\n        знч V = новый '+ns+'::'+item+'.Объект(Ссылка = '+ns+'::'+item+'.ПолучитьСсылку(новый Ууид("11111111-1111-4111-8111-111111111111")), '+key+' = 1, '+amount+' = 20, Label = "a", Day = новый Дата(2024, 2, 28))\n        V.Записать()\n        Seen = Q.Выполнить()\n        выбросить новый ИсключениеВалидации("rollback")\n    поймать E: ИсключениеВалидации\n        возврат {"old": Old, "seen": Seen, "after": Q.Выполнить(), "calls": Calls}\n    ;\n;\n'
  (root/'Entry').mkdir(exist_ok=True);(root/'Entry'/(main+'.xbsl')).write_text(source)


def build_documented():
 """Execute exact review-template query texts against authored metadata."""
 root=ROOT/'documented';root.mkdir(exist_ok=True)
 (root/'Проект.yaml').write_text('Имя: review041\nПоставщик: Teacher\nРежимСовместимости: "9.3"\n')
 for ns in ('Data','Other'):
  p=root/ns;p.mkdir(exist_ok=True)
  (p/'Item.yaml').write_text(yaml.safe_dump({'ВидЭлемента':'Справочник','Имя':'Item','ОбластьВидимости':'ВПроекте','Реквизиты':[{'Имя':'Key','Тип':'Число'},{'Имя':'Label','Тип':'Строка'},{'Имя':'Total','Тип':'Число'},{'Имя':'Flag','Тип':'Булево'}]},allow_unicode=True,sort_keys=False))
 source=''
 for label in ('computed-projection','group','having','case','combined-join-group-null'):
  text=(ROOT.parent/'storage-query-fill/documented-forms'/(label+'.xbql')).read_text()
  method=label.title().replace('-','')
  source+='метод '+method+'(): Объект\n    возврат Запрос{'+text+'}.Выполнить()\n;\n'
 (root/'Entry').mkdir(exist_ok=True);(root/'Entry/Main.xbsl').write_text(source)

if __name__=='__main__':
 main()
 build_documented()
