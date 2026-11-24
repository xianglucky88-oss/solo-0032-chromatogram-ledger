def concentration(assignment, curve):
    if assignment['status'] != 'matched' or curve['status'] != 'ready':
        return None
    return (assignment['area'] - curve['intercept']) / curve['slope']


def inside_range(concentration_value, curve):
    minimum, maximum = curve['levels'][0], curve['levels'][-1]
    epsilon = max(abs(minimum), abs(maximum)) * 1e-12
    return minimum - epsilon <= concentration_value <= maximum + epsilon
