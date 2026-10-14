"""One storage boundary for a complete UI/panel scenario, executed by Script."""


def enabled(plan):
    return bool(plan.storage and plan.storage.config.get('transaction'))


def render(plan, statements):
    """Keep observation variables outside the block; never capture technical faults."""
    if not enabled(plan):
        return statements
    business = 'ОшибкаСценария это ИсключениеНедопустимоеСостояние'
    if 'ИсключениеВалидации' in plan.exception_types:
        business += ' или ОшибкаСценария это ТестБизнесИсключения.ИсключениеВалидации'
    exception_type = ('(ОшибкаСценария это ТестБизнесИсключения.ИсключениеВалидации ? '
                      '"ИсключениеВалидации" : ОшибкаСценария.ПолучитьТип().ВСтроку())'
                      if 'ИсключениеВалидации' in plan.exception_types
                      else 'ОшибкаСценария.ПолучитьТип().ВСтроку()')
    return ('    пер ИсходТранзакции = "committed"\n'
            '    пер ОшибкаТранзакции: Объект? = Неопределено\n'
            '    ТестСессия.Начать()\n    попытка\n'
            + ''.join('    ' + line + '\n' for line in statements.splitlines())
            + '        ТестСессия.ПроверитьСессию()\n        ТестСессия.Завершить()\n'
            '    поймать ОшибкаСценария: Исключение\n'
            '        ТестСессия.Откатить()\n'
            '        если не (' + business + ')\n            выбросить ОшибкаСценария\n        ;\n'
            '        ИсходТранзакции = "rolledBack"\n'
            '        ОшибкаТранзакции = {"type": ' + exception_type + ', "message": ОшибкаСценария.Описание}\n'
            '    ;\n')


def observation():
    return '{"outcome": ИсходТранзакции, "exception": ОшибкаТранзакции}'
