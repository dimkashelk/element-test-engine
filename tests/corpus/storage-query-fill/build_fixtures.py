"""Reproducible portable declarations, deliberately independent of real names."""
from pathlib import Path
import yaml
ROOT=Path(__file__).resolve().parent

def write(path,data):
 path.parent.mkdir(parents=True,exist_ok=True)
 path.write_text(yaml.safe_dump(data,allow_unicode=True,sort_keys=False))

def main():
 for variant in ('ordinary','daily'):
  root=ROOT/variant
  write(root/'Проект.yaml',{'Имя':'Inventory' if variant=='ordinary' else 'History','Поставщик':'Teacher','РежимСовместимости':'9.0'})
  for ns in ('Data','Other'):
   write(root/ns/'Item.yaml',{'ВидЭлемента':'Справочник','Имя':'Item','Ид':'11111111-1111-4111-8111-111111111111','ОбластьВидимости':'ВПроекте','Реквизиты':[{'Имя':'Label','Тип':'Строка'},{'Имя':'Total','Тип':'Число'},{'Имя':'Optional','Тип':'Строка?'},{'Имя':'Link','Тип':ns+'::Item.Ссылка?'},{'Имя':'Flag','Тип':'Булево'},{'Имя':'Day','Тип':'Дата'},{'Имя':'Key','Тип':'Ууид'},{'Имя':'Kind','Тип':'Data::Category'}]})
   write(root/ns/'Card.yaml',{'ВидЭлемента':'Структура','Имя':'Card','ОбластьВидимости':'ВПроекте','Поля':[{'Имя':'Name','Тип':'Строка','Обязательное':True,'ТолькоЧтение':True},{'Имя':'Amount','Тип':'Число?'},{'Имя':'Note','Тип':'Строка?'},{'Имя':'Ref','Тип':ns+'::Item.Ссылка?'},{'Имя':'Second','Тип':ns+'::Item.Ссылка?'},{'Имя':'Default','Тип':'Строка','ЗначениеПоУмолчанию':'authored'},{'Имя':'Empty','Тип':'Строка'},{'Имя':'Flag','Тип':'Булево'},{'Имя':'Day','Тип':'Дата?'},{'Имя':'Key','Тип':'Ууид?'},{'Имя':'Kind','Тип':'Data::Category?'}]})
  write(root/'Data/Category.yaml',{'ВидЭлемента':'Перечисление','Имя':'Category','ОбластьВидимости':'ВПроекте','Элементы':[{'Имя':'First','ПоУмолчанию':True},{'Имя':'Second'}]})
  write(root/'Data/Book.yaml',{'ВидЭлемента':'РегистрСведений','Имя':'Book','ОбластьВидимости':'ВПроекте','Периодичность':'День','Измерения':[{'Имя':'Label','Тип':'Строка'}],'Ресурсы':[{'Имя':'Total','Тип':'Число'}],'Реквизиты':[{'Имя':'Optional','Тип':'Строка?'},{'Имя':'Link','Тип':'Data::Item.Ссылка?'},{'Имя':'Flag','Тип':'Булево'},{'Имя':'Day','Тип':'Дата'},{'Имя':'Key','Тип':'Ууид'},{'Имя':'Kind','Тип':'Data::Category'}]})
  source='Data::Item' if variant=='ordinary' else 'Data::Book.СрезПоследних(%D)'
  query='ВЫБРАТЬ ПЕРВЫЕ 2 P.Total КАК Amount, P.Link КАК Ref, P.Label КАК Name, P.Optional КАК Note, P.Link КАК Second, P.Flag КАК Flag, P.Day КАК Day, P.Key КАК Key, P.Kind КАК Kind ЗАПОЛНИТЬ Result ИЗ '+source+' КАК P УПОРЯДОЧИТЬ ПО P.Total ВОЗР'
  methods='''импорт Data::Card как Result
импорт Service::Helper как Helper
метод Rows(D: Дата): Массив<Result>
    возврат Запрос{QUERY}.Выполнить()
;
метод Single(D: Дата): Result?
    возврат Rows(D).ЕдинственныйИлиНеопределено()
;
метод Identity(Value: Result): Result
    возврат Helper.Identity(Value)
;
метод Construct(): Массив<Result>
    знч Value = новый Result(Name = "new", Amount = 9)
    возврат [Identity(Value)]
;
метод Detached(D: Дата): Объект
    знч Old = Rows(D)
    знч Fresh = Rows(D)
    Fresh[0].Amount = 999
    если Fresh[1].Ref != Неопределено
        Fresh[1].Ref.Идентификатор = новый Ууид("33333333-3333-4333-8333-333333333333")
    ;
    возврат {"old": Old, "fresh": Fresh, "again": Rows(D)}
;
метод Neighbor(): Other::Card
    возврат Запрос{ВЫБРАТЬ Label КАК Name ЗАПОЛНИТЬ Other::Card ИЗ Other::Item}.Выполнить().Единственный()
;
метод Collision(D: Дата): Объект
    возврат {"local": Rows(D), "other": Neighbor()}
;
метод Unsupported(): Объект
    возврат Запрос{ВЫБРАТЬ Label КАК Missing ЗАПОЛНИТЬ Result ИЗ Data::Item}.Выполнить()
;
'''.replace('QUERY',query)
  capture_source='Data::Item' if variant=='ordinary' else 'Data::Book.СрезПоследних(%{Helper.Date(Calls, D)})'
  write_source='    знч Value = новый Data::Item.Объект(Ссылка = Data::Item.ПолучитьСсылку(новый Ууид("11111111-1111-4111-8111-111111111111")), Label = L, Total = N)\n    Value.Записать()\n' if variant=='ordinary' else '    знч R = новый Data::Book.НаборЗаписей()\n    R.Фильтр.Установить(Label = L)\n    R.ДобавитьЗапись(Период = D, Label = L, Total = N, Optional = Неопределено, Link = Неопределено, Flag = Ложь, Day = D, Key = новый Ууид("11111111-1111-4111-8111-111111111111"), Kind = Data::Category.First)\n    R.Записать()\n'
  methods += 'метод Write(D: Дата, L: Строка, N: Число)\nWRITE;\nметод State(D: Дата, L: Строка): Объект\n    знч Calls = новый Массив<Строка>()\n    знч Q = Запрос{ВЫБРАТЬ Label КАК Name, Total КАК Amount ЗАПОЛНИТЬ Result ИЗ SOURCE ГДЕ Label == %{Helper.Label(Calls, L)}}\n    знч Old = Q.Выполнить()\n    пер Seen = новый Массив<Result>()\n    пер Again = новый Массив<Result>()\n    попытка\n        исп Транзакции.Начать()\n        Write(D, L, 17)\n        Seen = Q.Выполнить()\n        Seen[0].Amount = 700\n        Again = Q.Выполнить()\n        выбросить новый ИсключениеВалидации("rollback")\n    поймать E: ИсключениеВалидации\n        возврат {"old": Old, "seen": Seen, "again": Again, "after": Q.Выполнить(), "calls": Calls}\n    ;\n;\nметод Swallow(D: Дата): Строка\n    попытка\n        Rows(D)\n    поймать E: Исключение\n        возврат "caught"\n    ;\n    возврат "ok"\n;\n'.replace('WRITE',write_source).replace('SOURCE',capture_source)
  (root/'Entry').mkdir(exist_ok=True);(root/'Entry/Main.xbsl').write_text(methods)
  (root/'Service').mkdir(exist_ok=True);(root/'Service/Helper.xbsl').write_text('импорт Data::Card как Shape\n@Глобально\nметод Identity(Value: Shape): Shape\n    возврат Value\n;\n')
  with (root/'Service/Helper.xbsl').open('a') as f:
   f.write('@Глобально\nметод Date(Calls: Массив<Строка>, D: Дата): Дата\n    Calls.Добавить("date")\n    возврат D\n;\n@Глобально\nметод Label(Calls: Массив<Строка>, L: Строка): Строка\n    Calls.Добавить("label")\n    возврат L\n;\n')

if __name__=='__main__':main()
