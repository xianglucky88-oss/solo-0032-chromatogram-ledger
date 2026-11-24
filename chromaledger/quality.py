from .quantification import concentration, inside_range


def evaluate_qc(assignment, curve, expected, tolerance):
    observed = concentration(assignment, curve)
    evidence = {'observed': observed, 'expected': expected, 'tolerance': tolerance, 'relative_error': None}
    if observed is None:
        return {**evidence, 'status': 'unresolved'}
    relative_error = abs(observed - expected) / expected
    evidence['relative_error'] = relative_error
    if not inside_range(expected, curve) or not inside_range(observed, curve):
        return {**evidence, 'status': 'out_of_range'}
    return {**evidence, 'status': 'passed' if relative_error <= tolerance + 1e-12 else 'failed'}
