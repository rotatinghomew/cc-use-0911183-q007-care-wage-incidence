import json
import unittest
import urllib.error
import urllib.request
from http.server import HTTPServer
from threading import Thread

from paypolicy.app import Application
from paypolicy.cohort import cohort_comparison
from paypolicy.scheme import save_scheme, validate_scheme
from paypolicy.simulation import add_months, allocate, compare_schemes, simulate
from paypolicy.store import Store
from paypolicy.submission import reconcile_submission, submit_scheme
from paypolicy.web import handler_for

PLAN = {
    'id': 'plan',
    'name': '测试方案',
    'effective': '2026-10-15',
    'rules': {
        'rates': {'senior': 4500},
        'skill_allowance_cents_per_hour': {'senior': 500},
        'teaching_uplift_percent': 10,
        'pay_lag_months': 1,
    },
}


class SimulationTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(':memory:')
        Application(self.store).seed()

    def tearDown(self):
        self.store.close()

    def plan(self, **overrides):
        scheme = dict(PLAN)
        scheme.update(overrides)
        return scheme

    def people(self, result):
        return {row['person']: row for row in result['people']}

    def test_simulation_does_not_touch_payroll_records(self):
        kinds = ('shifts', 'skills', 'teaching', 'payments', 'rates', 'policies', 'schemes')
        before = {kind: self.store.all(kind) for kind in kinds}
        revisions = {kind: {str(row['id']): self.store.revision(kind, row['id']) for row in rows} for kind, rows in before.items()}
        simulate(self.store, self.plan(), 2026)
        for kind, rows in before.items():
            self.assertEqual(self.store.all(kind), rows)
            for row in rows:
                self.assertEqual(self.store.revision(kind, row['id']), revisions[kind][str(row['id'])])

    def test_person_level_diff_and_decomposition(self):
        result = simulate(self.store, self.plan(), 2026)
        people = self.people(result)
        p1 = people['p1']
        self.assertEqual(p1['baseline_cents'], 59000)
        self.assertEqual(p1['scheme_cents'], 67300)
        self.assertEqual(p1['diff_cents'], 8300)
        self.assertEqual(p1['components'], {'base_rule': 4000, 'skill': 4000, 'teaching': 300, 'floor': 0, 'cap': 0})
        self.assertEqual(sum(p1['components'].values()), p1['diff_cents'])
        self.assertEqual(people['p2']['diff_cents'], 0)
        self.assertEqual(result['totals']['gainers'], 1)
        self.assertEqual(result['totals']['losers'], 0)
        self.assertEqual(result['drivers'], {'工时': 0, '技能结构': 4000, '分配规则': 4300})
        self.assertTrue(result['reconciliation']['consistent'])

    def test_skill_before_and_after_not_mixed(self):
        scheme = self.plan(effective='2026-10-01', rules={'skill_allowance_cents_per_hour': {'senior': 500}})
        result = simulate(self.store, scheme, 2026)
        p1 = self.people(result)['p1']
        self.assertEqual(p1['components']['skill'], 4000)
        self.assertEqual(p1['diff_cents'], 4000)

    def test_scheme_effective_date_splits_rules(self):
        result = simulate(self.store, self.plan(effective='2026-11-01'), 2026)
        self.assertEqual(result['totals']['diff_cents'], 0)

    def test_teaching_credited_once_to_mentor_only(self):
        result = simulate(self.store, self.plan(), 2026)
        people = self.people(result)
        self.assertEqual(people['p2']['components']['teaching'], 0)
        self.assertEqual(people['p1']['components']['teaching'], 300)
        self.assertEqual(result['composition']['baseline']['teaching_cents'], 3000)
        self.assertEqual(result['composition']['scheme']['teaching_cents'], 3300)

    def test_unconfirmed_teaching_ignored(self):
        self.store.put('teaching', {'id': 't2', 'mentor': 'p1', 'learners': [], 'month': '2026-10', 'confirmed': False, 'approved_cents': 99999})
        result = simulate(self.store, self.plan(), 2026)
        self.assertEqual(result['composition']['scheme']['teaching_cents'], 3300)

    def test_floor_topup_merges_sites_within_month(self):
        self.store.put('shifts', {'id': 'x1', 'person': 'p2', 'site': 'east', 'date': '2026-10-20', 'minutes': 60, 'confirmed': True})
        self.store.put('shifts', {'id': 'x2', 'person': 'p2', 'site': 'west', 'date': '2026-10-21', 'minutes': 60, 'confirmed': True})
        scheme = self.plan(effective='2026-01-01', rules={'rates': {'basic': 2500}})
        result = simulate(self.store, scheme, 2026)
        p2 = self.people(result)['p2']
        self.assertEqual(p2['components']['floor'], 2000)
        self.assertEqual(p2['components']['base_rule'], -2000)
        self.assertEqual(p2['diff_cents'], 0)
        self.assertTrue(result['reconciliation']['consistent'])

    def test_annual_cap_withholds_cumulatively(self):
        for index, month in enumerate(['2026-01', '2026-03', '2026-05']):
            self.store.put('teaching', {'id': f'cap{index}', 'mentor': 'p9', 'learners': [], 'month': month, 'confirmed': True, 'approved_cents': 50000})
        scheme = self.plan(effective='2026-01-01', rules={'teaching_uplift_percent': 10})
        result = simulate(self.store, scheme, 2026)
        p9 = self.people(result)['p9']
        self.assertEqual(p9['baseline_cents'], 120000)
        self.assertEqual(p9['scheme_cents'], 120000)
        self.assertEqual(p9['components']['teaching'], 15000)
        self.assertEqual(p9['components']['cap'], -15000)
        self.assertEqual(sum(p9['components'].values()), p9['diff_cents'])
        self.assertEqual(result['composition']['scheme']['withheld_cents'], 45000)
        sites = {row['site']: row for row in result['site_costs']}
        self.assertEqual(sites['central']['baseline_cents'], 120000)
        self.assertEqual(sites['central']['diff_cents'], 0)

    def test_teaching_outside_year_ignored_and_missing_policy_rejected(self):
        self.store.put('teaching', {'id': 'next', 'mentor': 'p1', 'learners': [], 'month': '2027-01', 'confirmed': True, 'approved_cents': 50000})
        result = simulate(self.store, self.plan(), 2026)
        self.assertEqual(result['composition']['baseline']['teaching_cents'], 3000)
        with self.assertRaises(KeyError):
            simulate(self.store, self.plan(), 2027)

    def test_cash_month_view_and_spillover(self):
        self.store.put('shifts', {'id': 'dec', 'person': 'p1', 'site': 'east', 'date': '2026-12-10', 'minutes': 480, 'confirmed': True})
        result = simulate(self.store, self.plan(effective='2026-01-01'), 2026)
        cash = {row['cash_month']: row for row in result['by_cash_month']}
        self.assertTrue(cash['2027-01']['spillover'])
        self.assertFalse(cash['2026-11']['spillover'])
        self.assertEqual(cash['2027-01']['diff_cents'], 8000)
        self.assertEqual(result['budget']['cash_need_cents'], result['budget']['earning_need_cents'] - 8000)
        self.assertTrue(result['reconciliation']['consistent'])

    def test_disclosure_suppresses_small_groups_and_tiny_ratios(self):
        result = simulate(self.store, self.plan(), 2026)
        disclosed = result['disclosure']['groups']
        self.assertEqual(len(disclosed), 1)
        self.assertTrue(disclosed[0]['suppressed'])
        self.assertNotIn('diff_cents', disclosed[0])
        internal = result['groups'][0]
        self.assertEqual(internal['people'], 2)
        self.assertEqual(internal['diff_cents'], 8300)
        for row in result['people']:
            self.assertTrue(row['ratio_suppressed'])
            self.assertIsNone(row['change_percent'])
        self.assertEqual(result['totals']['diff_cents'], 8300)

    def test_budget_pressure_and_neutral_variant(self):
        scheme = self.plan()
        scheme['budget_cap_cents'] = 4000
        result = simulate(self.store, scheme, 2026)
        budget = result['budget']
        self.assertEqual(budget['cash_need_cents'], 8300)
        self.assertEqual(budget['pressure_cents'], 4300)
        variant = budget['suggested_variant']
        self.assertIn('未采用任何公平定义', variant['note'])
        save_scheme(self.store, {'id': 'variant', 'effective': '2026-10-15', 'budget_cap_cents': 4000, 'rules': variant['rules']})
        fitted = simulate(self.store, self.store.get('schemes', 'variant'), 2026)
        self.assertLessEqual(fitted['budget']['cash_need_cents'], 4000)
        self.assertLessEqual(fitted['budget']['pressure_cents'], 0)

    def test_compare_schemes_lists_without_choosing(self):
        save_scheme(self.store, self.plan())
        save_scheme(self.store, self.plan(id='plan2', rules={'teaching_uplift_percent': 5}))
        value = compare_schemes(self.store, ['plan', 'plan2'], 2026)
        self.assertEqual([row['scheme'] for row in value['schemes']], ['plan', 'plan2'])
        self.assertIn('未按任何公平定义', value['note'])
        with self.assertRaises(ValueError):
            compare_schemes(self.store, [], 2026)

    def test_allocate_conserves_total(self):
        self.assertEqual(allocate(8300, {'east': 480, 'west': 480}), {'east': 4150, 'west': 4150})
        self.assertEqual(sum(allocate(8300, {'east': 180, 'west': 60}).values()), 8300)
        self.assertEqual(allocate(1, {'a': 1, 'b': 1}), {'a': 1, 'b': 0})
        self.assertEqual(allocate(0, {'a': 1}), {'a': 0})
        self.assertEqual(add_months('2026-12', 1), '2027-01')
        self.assertEqual(add_months('2026-01', 6), '2026-07')

    def test_cohort_separates_turnover_from_same_people_change(self):
        self.store.put('shifts', {'id': 'sep1', 'person': 'p1', 'site': 'east', 'date': '2026-09-10', 'minutes': 480, 'confirmed': True})
        self.store.put('shifts', {'id': 'sep3', 'person': 'p3', 'site': 'east', 'date': '2026-09-11', 'minutes': 480, 'confirmed': True})
        value = cohort_comparison(self.store, '2026-09', '2026-10')
        self.assertEqual(value['balanced']['people'], 1)
        self.assertEqual(value['balanced']['change_cents'], 32000)
        self.assertEqual(value['joiners'], {'people': 1, 'cents': 6000})
        self.assertEqual(value['leavers'], {'people': 1, 'cents': 24000})
        self.assertEqual(value['naive_total_change_cents'], 14000)
        self.assertEqual(value['turnover_effect_cents'], -18000)
        with self.assertRaises(ValueError):
            cohort_comparison(self.store, '2026-10', '2026-09')

    def test_scheme_validation(self):
        with self.assertRaises(ValueError):
            validate_scheme({'id': 'x', 'effective': '2026-13-01'})
        with self.assertRaises(ValueError):
            validate_scheme({'id': 'x', 'effective': '2026-01-01', 'rules': {'unknown': 1}})
        with self.assertRaises(ValueError):
            validate_scheme({'id': 'x', 'effective': '2026-01-01', 'budget_cap_cents': -1})
        with self.assertRaises(ValueError):
            validate_scheme({'id': 'x', 'effective': '2026-01-01', 'rules': {'teaching_uplift_percent': 101}})
        with self.assertRaises(ValueError):
            validate_scheme({'effective': '2026-01-01'})

    def test_submission_freezes_and_reconcile_explains_corrections(self):
        save_scheme(self.store, self.plan())
        submitted = submit_scheme(self.store, 'plan', 2026)
        self.assertEqual(submitted['submission'], 'plan:2026')
        self.assertEqual(self.store.get('schemes', 'plan')['status'], 'submitted')
        with self.assertRaises(ValueError):
            save_scheme(self.store, self.plan())
        with self.assertRaises(ValueError):
            submit_scheme(self.store, 'plan', 2026)
        self.store.put('shifts', dict(self.store.get('shifts', 's2'), minutes=240))
        report = reconcile_submission(self.store, 'plan:2026')
        self.assertEqual(report['changed_records'], {'shifts': ['s2']})
        self.assertEqual(report['frozen_totals']['diff_cents'], 8300)
        row = next(item for item in report['people'] if item['person'] == 'p1')
        self.assertEqual(row['frozen_diff_cents'], 8300)
        self.assertEqual(row['current_diff_cents'], 4300)
        self.assertEqual(row['delta_cents'], -4000)
        self.assertEqual(row['changed_records'], ['shifts/s2'])
        self.assertEqual(report['totals_delta_cents'], -4000)
        self.assertEqual(report['people_delta_sum_cents'], report['totals_delta_cents'])
        self.assertEqual(self.store.get('submissions', 'plan:2026')['result']['totals']['diff_cents'], 8300)

    def test_payment_correction_reported_as_ledger_only(self):
        save_scheme(self.store, self.plan())
        submit_scheme(self.store, 'plan', 2026)
        self.store.put('payments', dict(self.store.get('payments', 'p1'), cents=81000))
        report = reconcile_submission(self.store, 'plan:2026')
        self.assertEqual(report['changed_records'], {'payments': ['p1']})
        self.assertIsNotNone(report['ledger_note'])
        self.assertEqual(report['totals_delta_cents'], 0)


class HttpSchemeTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(':memory:')
        self.application = Application(self.store)
        self.application.seed()
        self.server = HTTPServer(('127.0.0.1', 0), handler_for(self.application))
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = 'http://127.0.0.1:' + str(self.server.server_port)

    def tearDown(self):
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()
        self.store.close()

    def get(self, path):
        with urllib.request.urlopen(self.base + path) as response:
            return json.load(response)

    def post(self, path, value):
        body = json.dumps(value).encode('utf-8')
        request = urllib.request.Request(self.base + path, data=body, headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(request) as response:
            return json.load(response)

    def test_simulation_endpoint(self):
        value = self.get('/api/simulation?scheme=skill2026&year=2026')
        self.assertEqual(value['totals']['diff_cents'], 4300)
        self.assertTrue(value['reconciliation']['consistent'])

    def test_unknown_scheme_is_404(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.get('/api/simulation?scheme=nope&year=2026')
        self.assertEqual(caught.exception.code, 404)

    def test_invalid_scheme_is_400(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.post('/api/scheme', {'id': 'bad', 'effective': 'not-a-date'})
        self.assertEqual(caught.exception.code, 400)

    def test_submit_then_edit_rejected_and_reconcile_available(self):
        submitted = self.post('/api/submission', {'scheme': 'skill2026', 'year': 2026})
        self.assertEqual(submitted['submission'], 'skill2026:2026')
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.post('/api/scheme', {'id': 'skill2026', 'effective': '2026-07-01'})
        self.assertEqual(caught.exception.code, 400)
        report = self.get('/api/reconcile?submission=skill2026:2026')
        self.assertEqual(report['totals_delta_cents'], 0)
        listing = self.get('/api/submissions')
        self.assertEqual([row['id'] for row in listing], ['skill2026:2026'])


if __name__ == '__main__':
    unittest.main()
