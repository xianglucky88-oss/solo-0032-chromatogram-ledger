import hashlib
import json

from .errors import Rejected


def canonical(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':'), sort_keys=True)


def digest(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def unique_pairs(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError('duplicate property')
        obj[key] = value
    return obj


def invalid_constant(_):
    raise ValueError('non-finite number')


def decode_upload(payload):
    try:
        return json.loads(payload.decode('utf-8'), object_pairs_hook=unique_pairs, parse_constant=invalid_constant)
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise Rejected('invalid_json', '需要 UTF-8 JSON，字段不能重复，数值必须有限。', status=400) from exc
