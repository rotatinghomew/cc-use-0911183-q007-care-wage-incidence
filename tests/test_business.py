import unittest
from paypolicy.app import Application
from paypolicy.store import Store
from paypolicy.earnings import skill_at, shift_wage, monthly_earnings
from paypolicy.distribution import observed_distribution, department_totals
from paypolicy.finance import payment_totals, save_payment, teaching_credits


class PayTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(':memory:')
        Application(self.store).seed()

    def tearDown(self):
        self.store.close()

    def test_skill_effective_day(self):
        self.assertEqual(skill_at(self.store, 'p1', '2026-10-14'), 'basic')
        self.assertEqual(skill_at(self.store, 'p1', '2026-10-15'), 'senior')

    def test_earnings_combine_sites(self):
        person = next(row for row in monthly_earnings(self.store, '2026-10') if row['person'] == 'p1')
        self.assertEqual(person['cents'], 56000)

    def test_unconfirmed_not_paid(self):
        self.assertNotIn('p3', [row['person'] for row in monthly_earnings(self.store, '2026-10')])

    def test_department_conserves_total(self):
        self.assertEqual(sum(department_totals(self.store, '2026-10').values()), observed_distribution(self.store, '2026-10')['cents'])

    def test_cash_and_earning_differ(self):
        self.assertEqual(payment_totals(self.store, '2026-10', 'cash_month')['cents'], 80000)
        self.assertEqual(payment_totals(self.store, '2026-10', 'earning_month')['cents'], 56000)

    def test_invalid_payment(self):
        row = dict(self.store.get('payments', 'p1'), cents=-1)
        with self.assertRaises(ValueError):
            save_payment(self.store, row)

    def test_unknown_basis(self):
        with self.assertRaises(ValueError):
            payment_totals(self.store, '2026-10', 'other')

    def test_teaching_not_credited_to_learner(self):
        self.assertEqual(teaching_credits(self.store)['t1']['mentor'], 'p1')

    def test_negative_shift(self):
        with self.assertRaises(ValueError):
            shift_wage(self.store, dict(self.store.get('shifts', 's1'), minutes=-1))

    def test_empty_distribution_has_no_mean(self):
        self.assertIsNone(observed_distribution(self.store, '2027-01')['mean_cents'])
