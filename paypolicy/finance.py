def save_payment(store, row):
    if not isinstance(row['cents'], int) or row['cents'] < 0:
        raise ValueError('发放金额须为非负整数分')
    if len(row['earning_month']) != 7 or len(row['cash_month']) != 7:
        raise ValueError('月份格式不合法')
    return store.put('payments', row)


def payment_totals(store, month, basis):
    if basis not in ['earning_month', 'cash_month']:
        raise ValueError('核算视角不合法')
    rows = [row for row in store.all('payments') if row[basis] == month]
    return {'month': month, 'basis': basis, 'cents': sum(row['cents'] for row in rows), 'payments': sorted(row['id'] for row in rows)}


def teaching_credits(store):
    return {row['id']: {'mentor': row['mentor'], 'cents': row['approved_cents']} for row in store.all('teaching') if row['confirmed']}
