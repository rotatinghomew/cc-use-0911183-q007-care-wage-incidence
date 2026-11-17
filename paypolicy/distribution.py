from statistics import mean, median
from .earnings import monthly_earnings


def observed_distribution(store, month):
    rows = monthly_earnings(store, month)
    amounts = [row['cents'] for row in rows]
    return {'month': month, 'people': len(rows), 'cents': sum(amounts), 'mean_cents': mean(amounts) if amounts else None, 'median_cents': median(amounts) if amounts else None}


def department_totals(store, month):
    from .earnings import shift_wage
    values = {}
    for row in store.all('shifts'):
        if row['confirmed'] and row['date'].startswith(month):
            values[row['site']] = values.get(row['site'], 0) + shift_wage(store, row)
    return values
