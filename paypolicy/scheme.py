"""薪酬方案的起草、校验与规则解析。

方案是独立的记录类型，测算只读取既有劳动记录，不改动真实工资单。
方案送审后保留计算起点，不可再修改；需要调整时另建方案编号。
"""
import datetime

SCHEME_STATUSES = ('draft', 'submitted')

RULE_KEYS = (
    'rates',
    'skill_allowance_cents_per_hour',
    'teaching_uplift_percent',
    'hourly_floor_cents',
    'annual_bonus_cap_cents',
    'pay_lag_months',
    'hours_bands_minutes',
    'ratio_min_baseline_cents',
)

DEFAULT_HOURS_BANDS_MINUTES = (6000, 18000)
DEFAULT_PAY_LAG_MONTHS = 1


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _check_date(value):
    if not isinstance(value, str):
        raise ValueError('生效日须为 YYYY-MM-DD 格式')
    try:
        datetime.date.fromisoformat(value)
    except ValueError:
        raise ValueError('生效日须为合法的 YYYY-MM-DD 日期')


def _check_money_map(value, label, minimum):
    if not isinstance(value, dict):
        raise ValueError(f'{label}须为按技能等级给出的对象')
    for level, cents in value.items():
        if not isinstance(level, str) or not _is_int(cents) or cents < minimum:
            raise ValueError(f'{label}的取值须为不小于{minimum}的整数分')


def validate_scheme(row):
    if not isinstance(row, dict):
        raise ValueError('方案须为对象')
    scheme_id = row.get('id')
    if not isinstance(scheme_id, str) or not scheme_id:
        raise ValueError('方案须有编号')
    name = row.get('name', scheme_id)
    if not isinstance(name, str):
        raise ValueError('方案名称须为文本')
    _check_date(row.get('effective'))
    status = row.get('status', 'draft')
    if status not in SCHEME_STATUSES:
        raise ValueError('方案状态不合法')
    cap = row.get('budget_cap_cents')
    if cap is not None and (not _is_int(cap) or cap < 0):
        raise ValueError('预算上限须为非负整数分或空')
    rules = row.get('rules', {})
    if not isinstance(rules, dict):
        raise ValueError('方案规则须为对象')
    unknown = sorted(set(rules) - set(RULE_KEYS))
    if unknown:
        raise ValueError('方案规则包含未知项: ' + ','.join(unknown))
    if 'rates' in rules:
        _check_money_map(rules['rates'], '费率', 1)
    if 'skill_allowance_cents_per_hour' in rules:
        _check_money_map(rules['skill_allowance_cents_per_hour'], '技能津贴', 0)
    if 'teaching_uplift_percent' in rules:
        value = rules['teaching_uplift_percent']
        if not _is_int(value) or not 0 <= value <= 100:
            raise ValueError('带教上浮须为 0 到 100 的整数百分点')
    if 'hourly_floor_cents' in rules:
        if not _is_int(rules['hourly_floor_cents']) or rules['hourly_floor_cents'] <= 0:
            raise ValueError('小时保底须为正整数分')
    if 'annual_bonus_cap_cents' in rules:
        if not _is_int(rules['annual_bonus_cap_cents']) or rules['annual_bonus_cap_cents'] < 0:
            raise ValueError('年度奖励封顶须为非负整数分')
    if 'pay_lag_months' in rules:
        value = rules['pay_lag_months']
        if not _is_int(value) or not 0 <= value <= 6:
            raise ValueError('现金滞后月数须为 0 到 6 的整数')
    if 'hours_bands_minutes' in rules:
        bands = rules['hours_bands_minutes']
        if not isinstance(bands, list) or not 1 <= len(bands) <= 5:
            raise ValueError('工时分档须为 1 到 5 个分界分钟数')
        if any(not _is_int(band) or band <= 0 for band in bands) or len(set(bands)) != len(bands):
            raise ValueError('工时分界须为互不相同的正整数分钟')
    if 'ratio_min_baseline_cents' in rules:
        if not _is_int(rules['ratio_min_baseline_cents']) or rules['ratio_min_baseline_cents'] < 0:
            raise ValueError('比例展示下限须为非负整数分')
    return {'id': scheme_id, 'name': name, 'effective': row['effective'], 'status': status, 'budget_cap_cents': cap, 'rules': rules}


def save_scheme(store, row):
    scheme = validate_scheme(row)
    if store.revision('schemes', scheme['id']) and store.get('schemes', scheme['id']).get('status') == 'submitted':
        raise ValueError('已送审方案保留计算起点，不可修改，请另建方案编号')
    return store.put('schemes', scheme)


def policy_for(store, year):
    for row in store.all('policies'):
        if row.get('year') == year:
            return row
    raise KeyError(f'没有找到 policies/{year}')


def resolve_rules(store, scheme, year):
    """把方案规则与年度虚构制度（保底、封顶）合并为可执行的完整口径。"""
    policy = policy_for(store, year)
    rules = scheme.get('rules', {})
    return {
        'rates': dict(rules.get('rates', {})),
        'skill_allowance': dict(rules.get('skill_allowance_cents_per_hour', {})),
        'teaching_uplift_percent': rules.get('teaching_uplift_percent', 0),
        'hourly_floor_cents': rules.get('hourly_floor_cents', policy['hourly_floor_cents']),
        'annual_bonus_cap_cents': rules.get('annual_bonus_cap_cents', policy['annual_bonus_cap_cents']),
        'pay_lag_months': rules.get('pay_lag_months', DEFAULT_PAY_LAG_MONTHS),
        'hours_bands_minutes': sorted(rules.get('hours_bands_minutes', list(DEFAULT_HOURS_BANDS_MINUTES))),
        'ratio_min_baseline_cents': rules.get('ratio_min_baseline_cents', policy['hourly_floor_cents'] * 100),
    }
