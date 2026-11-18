"""方案送审的冻结与更正对账。

送审时保存方案快照、输入数据版本指纹和测算结果，三者共同构成计算起点。
工资更正到来后，用冻结的方案快照在当前数据上重算并逐人说明新增差异及归属，
原送审结果保持不变。
"""
from .simulation import simulate

INPUT_KINDS = ('shifts', 'skills', 'teaching', 'rates', 'policies', 'payments')


def fingerprint(store):
    return {kind: {str(row['id']): store.revision(kind, row['id']) for row in store.all(kind)} for kind in INPUT_KINDS}


def submit_scheme(store, scheme_id, year):
    scheme = store.get('schemes', scheme_id)
    if scheme.get('status') == 'submitted':
        raise ValueError('方案已送审，不能重复送审')
    result = simulate(store, scheme, year)
    submission_id = f'{scheme_id}:{year}'
    record = {
        'id': submission_id,
        'scheme': scheme_id,
        'year': year,
        'scheme_snapshot': scheme,
        'data_revisions': fingerprint(store),
        'result': result,
    }
    revision = store.put('submissions', record)
    store.put('schemes', dict(scheme, status='submitted'))
    return {'submission': submission_id, 'revision': revision}


def _changed_keys(frozen, current):
    changed = {}
    for kind in INPUT_KINDS:
        keys = set(frozen[kind]) | set(current[kind])
        diff = sorted(key for key in keys if frozen[kind].get(key) != current[kind].get(key))
        if diff:
            changed[kind] = diff
    return changed


def _attribute(store, changed):
    people = {}
    removed = {}
    global_changes = []
    for kind, keys in changed.items():
        for key in keys:
            try:
                row = store.get(kind, key)
            except KeyError:
                removed.setdefault(kind, []).append(key)
                continue
            if kind in ('rates', 'policies'):
                global_changes.append(f'{kind}/{key}')
                continue
            if kind == 'teaching':
                affected = [row['mentor']]
            else:
                affected = [row.get('person')]
            for person in affected:
                if person is not None:
                    people.setdefault(person, []).append(f'{kind}/{key}')
    return people, removed, global_changes


def reconcile_submission(store, submission_id):
    submission = store.get('submissions', submission_id)
    frozen = submission['data_revisions']
    current = fingerprint(store)
    changed = _changed_keys(frozen, current)
    fresh = simulate(store, submission['scheme_snapshot'], submission['year'])
    old_people = {row['person']: row for row in submission['result']['people']}
    new_people = {row['person']: row for row in fresh['people']}
    attribution, removed, global_changes = _attribute(store, changed)
    rows = []
    for person in sorted(set(old_people) | set(new_people)):
        old = old_people.get(person)
        new = new_people.get(person)
        old_diff = old['diff_cents'] if old else 0
        new_diff = new['diff_cents'] if new else 0
        records = sorted(attribution.get(person, []))
        if old_diff == new_diff and not records:
            continue
        rows.append({
            'person': person,
            'frozen_diff_cents': old_diff,
            'current_diff_cents': new_diff,
            'delta_cents': new_diff - old_diff,
            'changed_records': records,
        })
    ledger_changes = changed.get('payments', [])
    return {
        'submission': submission_id,
        'frozen_totals': submission['result']['totals'],
        'current_totals': fresh['totals'],
        'totals_delta_cents': fresh['totals']['diff_cents'] - submission['result']['totals']['diff_cents'],
        'changed_records': changed,
        'removed_records': removed,
        'global_changes': global_changes,
        'people': rows,
        'people_delta_sum_cents': sum(row['delta_cents'] for row in rows),
        'ledger_note': 'payments 为现金台账更正，不影响方案测算，仅用于实付对账' if ledger_changes else None,
        'note': '送审结果保持不变，本表说明更正带来的新增差异及其归属',
    }
