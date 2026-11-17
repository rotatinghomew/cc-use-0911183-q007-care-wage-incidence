def skill_at(store, person, day):
    rows = [row for row in store.all('skills') if row['person'] == person and row['effective'] <= day]
    return max(rows, key=lambda row: row['effective'])['level'] if rows else 'basic'


def shift_wage(store, row):
    if row['minutes'] < 0:
        raise ValueError('工作分钟不能为负')
    level = skill_at(store, row['person'], row['date'])
    rate = store.get('rates', level)['cents_per_hour']
    return (row['minutes'] * rate + 30) // 60


def monthly_earnings(store, month):
    values = {}
    for row in store.all('shifts'):
        if row['confirmed'] and row['date'].startswith(month):
            person = values.setdefault(row['person'], {'person': row['person'], 'minutes': 0, 'cents': 0})
            person['minutes'] += row['minutes']
            person['cents'] += shift_wage(store, row)
    return sorted(values.values(), key=lambda row: row['person'])
