"""观察数据的平衡面板：把人员更替带来的平均变化与同人同期变化分开。"""
from statistics import mean, median

from .earnings import monthly_earnings


def cohort_comparison(store, month_a, month_b):
    if month_a >= month_b:
        raise ValueError('起始月份须早于对比月份')
    earnings_a = {row['person']: row['cents'] for row in monthly_earnings(store, month_a)}
    earnings_b = {row['person']: row['cents'] for row in monthly_earnings(store, month_b)}
    both = sorted(set(earnings_a) & set(earnings_b))
    joiners = sorted(set(earnings_b) - set(earnings_a))
    leavers = sorted(set(earnings_a) - set(earnings_b))
    deltas = [earnings_b[person] - earnings_a[person] for person in both]
    balanced_change = sum(deltas)
    naive_change = sum(earnings_b.values()) - sum(earnings_a.values())
    return {
        'from': month_a,
        'to': month_b,
        'balanced': {
            'people': len(both),
            'change_cents': balanced_change,
            'mean_change_cents': mean(deltas) if deltas else None,
            'median_change_cents': median(deltas) if deltas else None,
            'gainers': sum(1 for delta in deltas if delta > 0),
            'losers': sum(1 for delta in deltas if delta < 0),
        },
        'joiners': {'people': len(joiners), 'cents': sum(earnings_b[person] for person in joiners)},
        'leavers': {'people': len(leavers), 'cents': sum(earnings_a[person] for person in leavers)},
        'naive_total_change_cents': naive_change,
        'turnover_effect_cents': naive_change - balanced_change,
        'note': '平衡面板只含两月均在册人员；人员更替效应单独列示，不计入政策效果',
    }
