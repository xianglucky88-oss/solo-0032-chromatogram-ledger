from .analysis import analyze
from .encoding import canonical, digest
from .errors import Rejected
from .peak_csv import join_peak_table
from .sequence import parse_document


def prepare(value):
    document = parse_document(join_peak_table(value))
    text = canonical(document)
    return document, text, digest(text), analyze(document)


def preview(value):
    document, _, fingerprint, analysis = prepare(value)
    return {'id': document['id'], 'input_hash': fingerprint, 'counts': analysis['counts'],
            'injection_count': len(document['injections']), 'target_count': len(document['method']['targets'])}


def ingest(conn, value):
    document, text, fingerprint, report = prepare(value)
    conn.execute('BEGIN IMMEDIATE')
    existing = conn.execute('SELECT input_hash FROM runs WHERE id = ?', (document['id'],)).fetchone()
    if existing:
        if existing['input_hash'] != fingerprint:
            raise Rejected('run_conflict', '此批次编号已用于不同的输入。请另用批次编号。', status=409)
        return {'id': document['id'], 'input_hash': fingerprint, 'reused': True}
    conn.execute('''
        INSERT INTO runs (id, title, method_id, input_hash, source, report)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (document['id'], document['label'], document['method']['id'], fingerprint, text, canonical(report)))
    return {'id': document['id'], 'input_hash': fingerprint, 'reused': False}
