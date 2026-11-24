import csv
from collections import defaultdict
from io import StringIO
import re

from .encoding import decode_upload
from .errors import Rejected
from .fields import array, key, object_fields, require

COLUMNS = ['injection_id', 'peak_id', 'time', 'area', 'saturated']
DECIMAL = re.compile(r'[0-9]{1,12}(?:\.[0-9]{1,12})?\Z')


def decimal(value, path):
    require(DECIMAL.fullmatch(value), path, '须使用非负十进制数，且小数点为英文句点')
    return float(value)


def parse_peak_table(text, injections):
    table = defaultdict(list)
    stream = csv.reader(StringIO(text, newline=''), strict=True)
    try:
        require(next(stream, []) == COLUMNS, 'peaks_csv', f'表头须为 {", ".join(COLUMNS)}')
        for row in stream:
            path = f'peaks_csv 第 {stream.line_num} 行'
            require(len(row) == len(COLUMNS), path, '列数不正确')
            injection_id, peak_id, time, area, saturated = row
            require(injection_id in injections, path, '找不到对应进样编号')
            require(saturated in ('0', '1'), path, '饱和列须为 0 或 1')
            peaks = table[injection_id]
            require(len(peaks) < 192, path, '单次进样最多 192 个峰')
            peaks.append({'id': key(peak_id, path), 'time': decimal(time, path),
                          'area': decimal(area, path), 'saturated': saturated == '1'})
    except csv.Error as exc:
        raise Rejected('invalid_peak_table', f'无法读取峰表第 {stream.line_num} 行。') from exc
    return table


def join_peak_table(value):
    if not isinstance(value, dict) or value.get('format') != 'peak-csv-v1':
        return value
    object_fields(value, '导入包', ('format', 'metadata_json', 'peaks_csv'))
    metadata, peak_table = value['metadata_json'], value['peaks_csv']
    require(isinstance(metadata, str) and isinstance(peak_table, str), '导入包', '序列和峰表内容应为文本')
    try:
        document = decode_upload(metadata.encode('utf-8'))
    except UnicodeError as exc:
        raise Rejected('invalid_json', '序列须使用 UTF-8 JSON。', status=400) from exc
    object_fields(document, '批次', ('id', 'label', 'method', 'injections'))
    sequence = array(document['injections'], 'injections', 1, 128)
    injections = set()
    for index, row in enumerate(sequence):
        path = f'injections[{index}]'
        object_fields(row, path, ('id', 'order', 'kind'), ('expected', 'sample_id', 'dilution'))
        injection_id = key(row['id'], f'{path}.id')
        require(injection_id not in injections, path, '进样编号重复')
        injections.add(injection_id)
    peaks = parse_peak_table(peak_table, injections)
    return {**document, 'injections': [{**row, 'peaks': peaks.get(row['id'], [])} for row in sequence]}
