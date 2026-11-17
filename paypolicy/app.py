from pathlib import Path
import json

from .earnings import *
from .distribution import *
from .finance import *


class Application:
    def __init__(self, store):
        self.store = store

    def seed(self):
        directory = Path(__file__).resolve().parent.parent / 'fixtures'
        for path in sorted(directory.glob('*.json')):
            if not self.store.all(path.stem):
                self.store.put_many(path.stem, json.loads(path.read_text(encoding='utf-8')))

    def demo(self):
        return {'earnings': monthly_earnings(self.store, '2026-10'), 'distribution': observed_distribution(self.store, '2026-10'), 'departments': department_totals(self.store, '2026-10'), 'cash': payment_totals(self.store, '2026-10', 'cash_month')}

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
        raise KeyError('接口不存在')
