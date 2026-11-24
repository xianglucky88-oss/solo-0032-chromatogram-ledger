def evaluate_blank(assignment, limit):
    code = assignment['status']
    area = assignment['area']
    if code == 'missing' or (code == 'matched' and area <= limit):
        status = 'clear'
    elif code == 'matched':
        status = 'contaminated'
    else:
        status = 'unresolved'
    return {'status': status, 'limit': limit, 'area': area, 'peak_status': code}
