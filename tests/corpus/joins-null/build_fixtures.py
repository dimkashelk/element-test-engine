"""Portable sources with independently authored answers in join_fixtures.py."""
from pathlib import Path
import yaml
ROOT=Path(__file__).resolve().parent

def write(p,data):
 p.parent.mkdir(parents=True,exist_ok=True);p.write_text(yaml.safe_dump(data,allow_unicode=True,sort_keys=False))

def main():
 for variant,ns,main,left,right,key,amount in [('ordinary','Data','Main','Item','Entry','Key','Amount'),('renamed','Учет','Пуск','Клиенты','Записи','Номер','Сумма')]:
  root=ROOT/variant
  write(root/'Проект.yaml',{'Имя':variant,'Поставщик':'Teacher','РежимСовместимости':'9.3'})
  for name,kind in [(left,'Справочник'),(right,'Документ')]:
   write(root/ns/(name+'.yaml'),{'ВидЭлемента':kind,'Имя':name,'ОбластьВидимости':'ВПроекте','Реквизиты':[{'Имя':key,'Тип':'Число'},{'Имя':'Label','Тип':'Строка'},{'Имя':amount,'Тип':'Число'},{'Имя':'Note','Тип':'Строка?'},{'Имя':'Peer','Тип':ns+'::'+left+'.Ссылка?'}]})
  write(root/'Other'/ (left+'.yaml'),{'ВидЭлемента':'Справочник','Имя':left,'ОбластьВидимости':'ВПроекте','Реквизиты':[{'Имя':key,'Тип':'Число'},{'Имя':'Label','Тип':'Строка'}]})
  write(root/ns/'Card.yaml',{'ВидЭлемента':'Структура','Имя':'Card','ОбластьВидимости':'ВПроекте','Поля':[{'Имя':'Left','Тип':'Строка?'},{'Имя':'Right','Тип':'Строка?'},{'Имя':'Amount','Тип':'Число?'},{'Имя':'Note','Тип':'Строка?'},{'Имя':'Ref','Тип':ns+'::'+left+'.Ссылка?'},{'Имя':'Second','Тип':ns+'::'+left+'.Ссылка?'}]})
  prefix='импорт '+ns+'::Card как Result\n'
  l=ns+'::'+left;r=ns+'::'+right
  from_part=l+' КАК L JOIN '+r+' КАК R ПО L.'+key+' == R.'+key
  projection='L.Label КАК Left, R.Label КАК Right, R.'+amount+' КАК Amount, R.Note КАК Note, L.Ссылка КАК Ref, L.Ссылка КАК Second'
  source=prefix
  for method,join in [('Inner','ВНУТРЕННЕЕ СОЕДИНЕНИЕ'),('Left','ЛЕВОЕ СОЕДИНЕНИЕ'),('Right','ПРАВОЕ ВНЕШНЕЕ СОЕДИНЕНИЕ'),('Full','ПОЛНОЕ СОЕДИНЕНИЕ')]:
   source+='метод '+method+'(): Массив<Result>\n    возврат Запрос{ВЫБРАТЬ '+projection+' ЗАПОЛНИТЬ Result ИЗ '+from_part.replace('JOIN',join)+'}.Выполнить()\n;\n'
  base=from_part.replace('JOIN','ЛЕВОЕ СОЕДИНЕНИЕ')
  cases=[('Missing','R.'+key+' ЕСТЬ NULL'),('Present','R.'+key+' ЕСТЬ НЕ NULL'),('UnknownNot','НЕ (R.'+amount+' == 3)'),('NullComparison','R.'+amount+' == NULL'),('Or','R.'+key+' ЕСТЬ NULL ИЛИ R.'+amount+' == 3'),('FalseAnd','Ложь И НЕ (R.'+amount+' == 3)'),('OptionalMissing','R.Note == Неопределено')]
  for method,condition in cases:
   source+='метод '+method+'(): Массив<Result>\n    возврат Запрос{ВЫБРАТЬ '+projection+' ЗАПОЛНИТЬ Result ИЗ '+base+' ГДЕ '+condition+'}.Выполнить()\n;\n'
  source+='метод Coalesce(): Объект\n    возврат Запрос{ВЫБРАТЬ L.Label КАК Left, R.'+amount+'.ЗаменитьNull(0) КАК Amount, R.Note.ЗаменитьNull("missing") КАК Note, R.Note ЕСТЬ NULL КАК Absent ИЗ '+base+' УПОРЯДОЧИТЬ ПО Left, Amount}.Выполнить()\n;\n'
  source+='метод Default(): Объект\n    возврат Запрос{ВЫБРАТЬ L.Label КАК Left, R.'+amount+'.ЗаменитьNull() КАК Amount ИЗ '+base+' УПОРЯДОЧИТЬ ПО Left, Amount}.Выполнить()\n;\n'
  source+='метод Nested(): Объект\n    возврат Запрос{ВЫБРАТЬ L.Label КАК Left, R.Label КАК Right, O.Label КАК Other ИЗ '+base+' ПОЛНОЕ СОЕДИНЕНИЕ Other::'+left+' КАК O ПО O.'+key+' == R.'+key+'}.Выполнить()\n;\n'
  source+='метод Self(): Объект\n    возврат Запрос{ВЫБРАТЬ L.Label КАК Left, R.Label КАК Right ИЗ '+l+' КАК L СОЕДИНЕНИЕ '+l+' КАК R ПО L.'+key+' == R.'+key+'}.Выполнить()\n;\n'
  source+='метод RefJoin(): Объект\n    возврат Запрос{ВЫБРАТЬ L.Label КАК Left, R.Label КАК Right ИЗ '+l+' КАК L СОЕДИНЕНИЕ '+r+' КАК R ПО L.Ссылка == R.Peer}.Выполнить()\n;\n'
  source+='метод Detached(): Объект\n    знч Old = Left()\n    знч Fresh = Left()\n    Fresh[0].Ref.Идентификатор = новый Ууид("99999999-9999-4999-8999-999999999999")\n    Fresh[0].Amount = 700\n    возврат {"old": Old, "fresh": Fresh, "again": Left()}\n;\n'
  source+='метод Unsupported(): Объект\n    возврат Запрос{ВЫБРАТЬ L.Label КАК Left ИЗ '+base+' ГДЕ R.'+key+' В (1, 2)}.Выполнить()\n;\n'
  source+='метод Write(N: Число)\n    знч V = новый '+r+'.Объект(Ссылка = '+r+'.ПолучитьСсылку(новый Ууид("44444444-4444-4444-8444-444444444444")), '+key+' = 1, Label = "x", '+amount+' = N)\n    V.Записать()\n;\n'
  source+='метод Capture(Calls: Массив<Строка>, K: Число): Число\n    Calls.Добавить("key")\n    возврат K\n;\n'
  source+='метод State(K: Число): Объект\n    знч Calls = новый Массив<Строка>()\n    знч Q = Запрос{ВЫБРАТЬ '+projection+' ЗАПОЛНИТЬ Result ИЗ '+base+' ГДЕ L.'+key+' == %{Capture(Calls, K)}}\n    знч Old = Q.Выполнить()\n    пер Seen = новый Массив<Result>()\n    пер Again = новый Массив<Result>()\n    попытка\n        исп Транзакции.Начать()\n        Write(17)\n        Seen = Q.Выполнить()\n        Seen[0].Amount = 700\n        Again = Q.Выполнить()\n        выбросить новый ИсключениеВалидации("rollback")\n    поймать E: ИсключениеВалидации\n        возврат {"old": Old, "seen": Seen, "again": Again, "after": Q.Выполнить(), "calls": Calls}\n    ;\n;\n'
  source+='метод Swallow(): Строка\n    попытка\n        Left()\n    поймать E: Исключение\n        возврат "caught"\n    ;\n    возврат "ok"\n;\n'
  (root/'Entry').mkdir(exist_ok=True);(root/'Entry'/(main+'.xbsl')).write_text(source)
if __name__=='__main__':main()
