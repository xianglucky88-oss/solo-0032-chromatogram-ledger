from collections import Counter

from .assignment import map_injection
from .blank import evaluate_blank
from .calibration import fit_sequence
from .eligibility import decide_sample
from .quality import evaluate_qc

ENGINE = 'chromaledger-v1'


def analyze(document):
    method = document['method']
    injections = document['injections']
    mapped = {row['id']: map_injection(row, method) for row in injections}
    curves = fit_sequence(injections, mapped, method)
    blanks, checks = {}, {}
    results = []
    counts = Counter({'reportable': 0, 'held': 0})
    for row in injections:
        mapped_row = mapped[row['id']]
        result = {**{k: v for k, v in row.items() if k != 'peaks'}, **mapped_row, 'decisions': {}}
        for target in method['targets']:
            target_id = target['id']
            assignment = mapped_row['assignments'][target_id]
            curve = curves[target_id]
            if row['kind'] == 'blank':
                evidence = evaluate_blank(assignment, target['blank_limit'])
                blanks[target_id] = {**evidence, 'injection_id': row['id']}
            elif row['kind'] == 'qc':
                evidence = evaluate_qc(assignment, curve, row['expected'][target_id], method['qc_tolerance'])
                checks[target_id] = {**evidence, 'injection_id': row['id']}
            elif row['kind'] == 'sample':
                evidence = decide_sample(row, assignment, curve, blanks.get(target_id), checks.get(target_id))
                counts[evidence['status']] += 1
            else:
                evidence = {'status': 'used' if assignment['status'] == 'matched' else 'excluded'}
            result['decisions'][target_id] = evidence
        results.append(result)
    return {'engine': ENGINE, 'curves': curves, 'injections': results, 'counts': dict(counts)}
