from pathlib import Path
import json

from .earnings import *
from .distribution import *
from .finance import *
from .scheme import *
from .simulation import *
from .cohort import *
from .submission import *


class Application:
    def __init__(self, store):
        self.store = store

    def seed(self):
        directory = Path(__file__).resolve().parent.parent / 'fixtures'
        for path in sorted(directory.glob('*.json')):
            if not self.store.all(path.stem):
                self.store.put_many(path.stem, json.loads(path.read_text(encoding='utf-8')))

    def demo(self):
        value = {'earnings': monthly_earnings(self.store, '2026-10'), 'distribution': observed_distribution(self.store, '2026-10'), 'departments': department_totals(self.store, '2026-10'), 'cash': payment_totals(self.store, '2026-10', 'cash_month')}
        drafts = sorted(row['id'] for row in self.store.all('schemes') if row.get('status') == 'draft')
        if drafts:
            value['scheme_impact'] = compare_schemes(self.store, drafts, 2026)
        return value

    def check(self):
        value = self.demo()
        return {'status': 'ok', 'summary': value}

    def call(self, method, route, payload):
        if method == 'GET' and route == 'summary':
            return self.demo()
        if method == 'GET' and route == 'earnings':
            return monthly_earnings(self.store, payload['month'])
        if method == 'GET' and route == 'payments':
            return payment_totals(self.store, payload['month'], payload['basis'])
        if method == 'POST' and route == 'payment':
            return {'revision': save_payment(self.store, payload)}
        if method == 'GET' and route == 'schemes':
            return sorted(self.store.all('schemes'), key=lambda row: row['id'])
        if method == 'POST' and route == 'scheme':
            return {'revision': save_scheme(self.store, payload)}
        if method == 'GET' and route == 'simulation':
            return simulate(self.store, self.store.get('schemes', payload['scheme']), int(payload['year']))
        if method == 'GET' and route == 'compare':
            return compare_schemes(self.store, sorted(payload['schemes'].split(',')), int(payload['year']))
        if method == 'GET' and route == 'cohort':
            return cohort_comparison(self.store, payload['from'], payload['to'])
        if method == 'POST' and route == 'submission':
            return submit_scheme(self.store, payload['scheme'], int(payload['year']))
        if method == 'GET' and route == 'submissions':
            return [{'id': row['id'], 'scheme': row['scheme'], 'year': row['year'], 'totals': row['result']['totals']} for row in sorted(self.store.all('submissions'), key=lambda row: row['id'])]
        if method == 'GET' and route == 'submission':
            return self.store.get('submissions', payload['id'])
        if method == 'GET' and route == 'reconcile':
            return reconcile_submission(self.store, payload['submission'])
        raise KeyError('接口不存在')
