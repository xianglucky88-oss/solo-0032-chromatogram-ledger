import re

from .errors import Rejected

KEY = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,47}\Z')


def require(condition, where, detail):
    if not condition:
        raise Rejected('invalid_document', f'{where}：{detail}')


def object_fields(value, where, required, optional=()):
    require(isinstance(value, dict), where, '应为对象')
    absent = set(required) - value.keys()
    unknown = value.keys() - set(required) - set(optional)
    require(not absent, where, f'缺少字段 {", ".join(sorted(absent))}')
    require(not unknown, where, f'未知字段 {", ".join(sorted(unknown))}')
    return value


def key(value, where):
    require(isinstance(value, str) and KEY.fullmatch(value), where, '编号须为 1–48 个 ASCII 字母、数字、点、横线或下划线')
    return value


def label(value, where):
    require(isinstance(value, str) and 0 < len(value.strip()) <= 100 and value.isprintable() and all(ord(ch) < 0xD800 or ord(ch) > 0xDFFF for ch in value), where, '应为 1–100 个可显示字符')
    return value.strip()


def number(value, where, minimum, maximum, *, exclusive=False):
    ordered = type(value) in (int, float) and minimum <= value <= maximum
    require(ordered and (not exclusive or value > minimum), where, f'数值应在 {"(" if exclusive else "["}{minimum}, {maximum}]')
    result = float(value)
    return result if result else 0.0


def integer(value, where, minimum, maximum):
    require(type(value) is int and minimum <= value <= maximum, where, f'应为 {minimum} 至 {maximum} 的整数')
    return value


def array(value, where, minimum, maximum):
    require(isinstance(value, list) and minimum <= len(value) <= maximum, where, f'应有 {minimum} 至 {maximum} 条记录')
    return value


def unique(values, where):
    require(len(set(values)) == len(values), where, '编号重复')
