from .quantification import concentration, inside_range


def decide_sample(injection, assignment, curve, blank, qc):
    issues = []
    if curve['status'] != 'ready':
        issues.append('curve_unavailable')
    if assignment['status'] != 'matched':
        issues.append(assignment['status'])
    if blank is None:
        issues.append('blank_absent')
    elif blank['status'] != 'clear':
        issues.append('blank_blocked')
    if qc is None:
        issues.append('qc_absent')
    elif qc['status'] != 'passed':
        issues.append('qc_blocked')

    measured = concentration(assignment, curve)
    if measured is not None and not inside_range(measured, curve):
        issues.append('out_of_range')
    return {
        'status': 'held' if issues else 'reportable', 'issues': issues,
        'measured': measured,
        'reported': None if issues else measured * injection['dilution'],
        'blank_id': blank['injection_id'] if blank else None,
        'qc_id': qc['injection_id'] if qc else None,
    }
