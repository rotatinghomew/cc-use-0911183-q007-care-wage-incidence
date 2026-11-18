"""在同一套实际劳动记录上比较薪酬方案，不改动任何真实工资记录。

口径约定：
- 收入所属月按班次日期与带教月份归属；现金月按方案约定的滞后月数平移；
  方案生效日之前的劳动按现有规则计、之后按方案规则计，二者不混算。
- 技能等级按班次当天的生效等级取，取得技能前后的待遇不混算。
- 保底按人按月跨院合并后补齐；年度奖励封顶按人按年跨院累计；
  两者都按 policies 中的本地虚构制度解释。
- 带教奖励仅归导师且每笔只兑现一次；院区成本按班次承担方追溯，
  带教成本按导师当月工时摊入承担院区，当月无班次时归入 central（院本部）。
- 差异分解为工时（保底）、技能结构（技能津贴）、分配规则（费率、带教、封顶）
  三类来源，逐人逐月可加总对账。
"""
from .earnings import skill_at
from .scheme import policy_for, resolve_rules

COMPONENT_DRIVERS = {'base_rule': '分配规则', 'skill': '技能结构', 'teaching': '分配规则', 'floor': '工时', 'cap': '分配规则'}
COMPONENTS = tuple(COMPONENT_DRIVERS)


def add_months(month, count):
    year, number = (int(part) for part in month.split('-'))
    index = year * 12 + number - 1 + count
    return f'{index // 12}-{index % 12 + 1:02d}'


def allocate(total, weights):
    """把 total 按非负权重摊到各方，最大余数法，总额守恒、结果确定。"""
    keys = sorted(weights)
    if not keys or total == 0:
        return {key: 0 for key in keys}
    whole = sum(weights.values())
    shares = {key: total * weights[key] // whole for key in keys}
    leftover = total - sum(shares.values())
    order = sorted(keys, key=lambda key: (-(total * weights[key] - shares[key] * whole), key))
    for key in order[:leftover]:
        shares[key] += 1
    return shares


def _new_cell():
    return {'minutes': 0, 'base_b': 0, 'base_s': 0, 'skill': 0, 'teach_b': 0, 'teach_s': 0, 'floor_b': 0, 'floor_s': 0, 'withheld_b': 0, 'withheld_s': 0}


def _new_site_cell():
    return {'base_b': 0, 'base_s': 0, 'skill': 0, 'teach_b': 0, 'teach_s': 0, 'floor_b': 0, 'floor_s': 0, 'withheld_b': 0, 'withheld_s': 0}


def _band_label(index, bands):
    low = bands[index - 1] if index > 0 else 0
    high = bands[index] if index < len(bands) else None
    if high is None:
        return f'>={low // 60}小时'
    if low == 0:
        return f'<{high // 60}小时'
    return f'{low // 60}-{high // 60}小时'


def _fit_variant(rules, base_rates, cap, need):
    """预算不足时给出一种按增量等比收缩的可执行变体，仅展示、不代选。"""
    scale = cap * 1000000 // need
    rates = {}
    for level, base in sorted(base_rates.items()):
        scaled = base + (rules['rates'].get(level, base) - base) * scale // 1000000
        if scaled != base:
            rates[level] = scaled
    allowance = {level: cents * scale // 1000000 for level, cents in sorted(rules['skill_allowance'].items())}
    allowance = {level: cents for level, cents in allowance.items() if cents > 0}
    uplift = rules['teaching_uplift_percent'] * scale // 1000000
    variant_rules = {
        'hourly_floor_cents': rules['hourly_floor_cents'],
        'annual_bonus_cap_cents': rules['annual_bonus_cap_cents'],
        'pay_lag_months': rules['pay_lag_months'],
    }
    if rates:
        variant_rules['rates'] = rates
    if allowance:
        variant_rules['skill_allowance_cents_per_hour'] = allowance
    if uplift:
        variant_rules['teaching_uplift_percent'] = uplift
    return {'scale_ppm': scale, 'rules': variant_rules, 'note': '仅为按增量等比收缩的一种可执行方案，未采用任何公平定义；如需采用请另存为新方案后测算'}


def simulate(store, scheme, year):
    if not isinstance(year, int) or isinstance(year, bool):
        raise ValueError('年度须为整数')
    effective = scheme.get('effective')
    if not effective:
        raise ValueError('方案须含生效日')
    policy = policy_for(store, year)
    rules = resolve_rules(store, scheme, year)
    effective_month = effective[:7]
    base_rates = {row['id']: row['cents_per_hour'] for row in store.all('rates')}
    months = [f'{year}-{number:02d}' for number in range(1, 13)]
    prefix = f'{year}-'

    cells = {}
    site_cells = {}
    minutes_by_site = {}

    def cell_for(person, month):
        return cells.setdefault(person, {}).setdefault(month, _new_cell())

    def site_cell_for(site, month):
        return site_cells.setdefault((site, month), _new_site_cell())

    for row in store.all('shifts'):
        if not row['confirmed'] or not row['date'].startswith(prefix):
            continue
        minutes = row['minutes']
        if minutes < 0:
            raise ValueError('工作分钟不能为负')
        person, site, month = row['person'], row['site'], row['date'][:7]
        level = skill_at(store, person, row['date'])
        base_rate = base_rates[level]
        pay_b = (minutes * base_rate + 30) // 60
        if row['date'] >= effective:
            pay_s = (minutes * rules['rates'].get(level, base_rate) + 30) // 60
            skill = (minutes * rules['skill_allowance'].get(level, 0) + 30) // 60
        else:
            pay_s, skill = pay_b, 0
        cell = cell_for(person, month)
        cell['minutes'] += minutes
        cell['base_b'] += pay_b
        cell['base_s'] += pay_s
        cell['skill'] += skill
        site_cell = site_cell_for(site, month)
        site_cell['base_b'] += pay_b
        site_cell['base_s'] += pay_s
        site_cell['skill'] += skill
        sites = minutes_by_site.setdefault((person, month), {})
        sites[site] = sites.get(site, 0) + minutes

    for row in store.all('teaching'):
        if not row['confirmed'] or not row['month'].startswith(prefix):
            continue
        month = row['month']
        teach_b = row['approved_cents']
        teach_s = (teach_b * (100 + rules['teaching_uplift_percent'])) // 100 if month >= effective_month else teach_b
        cell = cell_for(row['mentor'], month)
        cell['teach_b'] += teach_b
        cell['teach_s'] += teach_s
        weights = minutes_by_site.get((row['mentor'], month)) or {'central': 1}
        for site, share in allocate(teach_b, weights).items():
            site_cell_for(site, month)['teach_b'] += share
        for site, share in allocate(teach_s, weights).items():
            site_cell_for(site, month)['teach_s'] += share

    for person, by_month in cells.items():
        for month, cell in by_month.items():
            if cell['minutes']:
                cell['floor_b'] = max(0, (cell['minutes'] * policy['hourly_floor_cents'] + 30) // 60 - cell['base_b'])
                cell['floor_s'] = max(0, (cell['minutes'] * rules['hourly_floor_cents'] + 30) // 60 - cell['base_s'])
            weights = minutes_by_site.get((person, month))
            if weights:
                for site, share in allocate(cell['floor_b'], weights).items():
                    site_cell_for(site, month)['floor_b'] += share
                for site, share in allocate(cell['floor_s'], weights).items():
                    site_cell_for(site, month)['floor_s'] += share
        used_b = used_s = 0
        for month in months:
            cell = by_month.get(month)
            if cell is None:
                continue
            bonus_b = cell['teach_b']
            bonus_s = cell['skill'] + cell['teach_s']
            allowed_b = min(bonus_b, max(0, policy['annual_bonus_cap_cents'] - used_b))
            allowed_s = min(bonus_s, max(0, rules['annual_bonus_cap_cents'] - used_s))
            cell['withheld_b'] = bonus_b - allowed_b
            cell['withheld_s'] = bonus_s - allowed_s
            used_b += allowed_b
            used_s += allowed_s
            weights_b = {site: sc['teach_b'] for (site, site_month), sc in site_cells.items() if site_month == month and sc['teach_b']}
            weights_s = {site: sc['skill'] + sc['teach_s'] for (site, site_month), sc in site_cells.items() if site_month == month and sc['skill'] + sc['teach_s']}
            for site, share in allocate(cell['withheld_b'], weights_b).items():
                site_cell_for(site, month)['withheld_b'] += share
            for site, share in allocate(cell['withheld_s'], weights_s).items():
                site_cell_for(site, month)['withheld_s'] += share

    people_rows = []
    by_earning = {month: {'baseline': 0, 'scheme': 0} for month in months}
    by_cash = {}
    components_total = {name: 0 for name in COMPONENTS}
    composition = {
        'baseline': {'base_cents': 0, 'floor_cents': 0, 'teaching_cents': 0, 'withheld_cents': 0},
        'scheme': {'base_cents': 0, 'floor_cents': 0, 'skill_cents': 0, 'teaching_cents': 0, 'withheld_cents': 0},
    }
    ratio_min = rules['ratio_min_baseline_cents']
    for person, by_month in cells.items():
        row = {'person': person, 'minutes': 0, 'baseline_cents': 0, 'scheme_cents': 0, 'components': {name: 0 for name in COMPONENTS}}
        for month, cell in by_month.items():
            baseline = cell['base_b'] + cell['floor_b'] + cell['teach_b'] - cell['withheld_b']
            scheme_total = cell['base_s'] + cell['floor_s'] + cell['skill'] + cell['teach_s'] - cell['withheld_s']
            comp = {
                'base_rule': cell['base_s'] - cell['base_b'],
                'skill': cell['skill'],
                'teaching': cell['teach_s'] - cell['teach_b'],
                'floor': cell['floor_s'] - cell['floor_b'],
                'cap': -(cell['withheld_s'] - cell['withheld_b']),
            }
            row['minutes'] += cell['minutes']
            row['baseline_cents'] += baseline
            row['scheme_cents'] += scheme_total
            for name, cents in comp.items():
                row['components'][name] += cents
            by_earning[month]['baseline'] += baseline
            by_earning[month]['scheme'] += scheme_total
            cash_month = add_months(month, rules['pay_lag_months'])
            cash = by_cash.setdefault(cash_month, {'baseline': 0, 'scheme': 0})
            cash['baseline'] += baseline
            cash['scheme'] += scheme_total
            composition['baseline']['base_cents'] += cell['base_b']
            composition['baseline']['floor_cents'] += cell['floor_b']
            composition['baseline']['teaching_cents'] += cell['teach_b']
            composition['baseline']['withheld_cents'] += cell['withheld_b']
            composition['scheme']['base_cents'] += cell['base_s']
            composition['scheme']['floor_cents'] += cell['floor_s']
            composition['scheme']['skill_cents'] += cell['skill']
            composition['scheme']['teaching_cents'] += cell['teach_s']
            composition['scheme']['withheld_cents'] += cell['withheld_s']
        row['diff_cents'] = row['scheme_cents'] - row['baseline_cents']
        row['change_percent'] = round(100 * row['diff_cents'] / row['baseline_cents'], 2) if row['baseline_cents'] >= ratio_min else None
        row['ratio_suppressed'] = row['baseline_cents'] < ratio_min
        for name, cents in row['components'].items():
            components_total[name] += cents
        people_rows.append(row)
    people_rows.sort(key=lambda row: row['person'])

    site_rows = {}
    for (site, month), cell in site_cells.items():
        baseline = cell['base_b'] + cell['floor_b'] + cell['teach_b'] - cell['withheld_b']
        scheme_total = cell['base_s'] + cell['floor_s'] + cell['skill'] + cell['teach_s'] - cell['withheld_s']
        row = site_rows.setdefault(site, {'site': site, 'baseline_cents': 0, 'scheme_cents': 0})
        row['baseline_cents'] += baseline
        row['scheme_cents'] += scheme_total
    sites = []
    for site in sorted(site_rows):
        row = site_rows[site]
        row['diff_cents'] = row['scheme_cents'] - row['baseline_cents']
        sites.append(row)

    bands = rules['hours_bands_minutes']
    groups = []
    for index in range(len(bands) + 1):
        members = [row for row in people_rows if sum(row['minutes'] >= band for band in bands) == index]
        if not members:
            continue
        baseline = sum(row['baseline_cents'] for row in members)
        diff = sum(row['diff_cents'] for row in members)
        groups.append({
            'band': _band_label(index, bands),
            'people': len(members),
            'minutes': sum(row['minutes'] for row in members),
            'baseline_cents': baseline,
            'scheme_cents': baseline + diff,
            'diff_cents': diff,
            'gainers': sum(1 for row in members if row['diff_cents'] > 0),
            'losers': sum(1 for row in members if row['diff_cents'] < 0),
            'components': {name: sum(row['components'][name] for row in members) for name in COMPONENTS},
            'change_percent': round(100 * diff / baseline, 2) if baseline >= ratio_min else None,
            'ratio_suppressed': baseline < ratio_min,
        })

    min_people = policy['disclosure_min_people']
    disclosure_groups = []
    for group in groups:
        if group['people'] < min_people:
            disclosure_groups.append({'band': group['band'], 'people': group['people'], 'suppressed': True})
        else:
            disclosure_groups.append({key: group[key] for key in ('band', 'people', 'baseline_cents', 'scheme_cents', 'diff_cents', 'gainers', 'losers', 'change_percent', 'ratio_suppressed')})

    total_baseline = sum(row['baseline_cents'] for row in people_rows)
    total_scheme = sum(row['scheme_cents'] for row in people_rows)
    totals = {
        'people': len(people_rows),
        'baseline_cents': total_baseline,
        'scheme_cents': total_scheme,
        'diff_cents': total_scheme - total_baseline,
        'gainers': sum(1 for row in people_rows if row['diff_cents'] > 0),
        'losers': sum(1 for row in people_rows if row['diff_cents'] < 0),
        'unchanged': sum(1 for row in people_rows if row['diff_cents'] == 0),
    }

    earning_rows = [{'month': month, 'baseline_cents': by_earning[month]['baseline'], 'scheme_cents': by_earning[month]['scheme'], 'diff_cents': by_earning[month]['scheme'] - by_earning[month]['baseline']} for month in months]
    cash_rows = [
        {'cash_month': month, 'baseline_cents': value['baseline'], 'scheme_cents': value['scheme'], 'diff_cents': value['scheme'] - value['baseline'], 'spillover': not month.startswith(prefix)}
        for month, value in sorted(by_cash.items())
    ]
    cash_need = sum(row['diff_cents'] for row in cash_rows if not row['spillover'])

    budget = {
        'earning_need_cents': totals['diff_cents'],
        'cash_need_cents': cash_need,
        'cap_cents': scheme.get('budget_cap_cents'),
        'basis_note': 'earning_need 为收入所属月口径；cash_need 为现金月口径，仅含落在本年度的现金月，不含上年递延',
    }
    if budget['cap_cents'] is not None:
        budget['pressure_cents'] = cash_need - budget['cap_cents']
        if budget['pressure_cents'] > 0 and cash_need > 0:
            budget['suggested_variant'] = _fit_variant(rules, base_rates, budget['cap_cents'], cash_need)
    else:
        budget['pressure_cents'] = None

    reconciliation = {
        'earning_months_diff_cents': sum(row['diff_cents'] for row in earning_rows),
        'cash_months_diff_cents': sum(row['diff_cents'] for row in cash_rows),
        'people_diff_cents': sum(row['diff_cents'] for row in people_rows),
        'sites_diff_cents': sum(row['diff_cents'] for row in sites),
        'components_diff_cents': sum(components_total.values()),
    }
    reconciliation['consistent'] = len(set(reconciliation.values())) == 1 and totals['diff_cents'] == reconciliation['people_diff_cents']

    return {
        'scheme': scheme['id'],
        'year': year,
        'effective': effective,
        'pay_lag_months': rules['pay_lag_months'],
        'cohort': {'people': len(people_rows), 'basis': '同一套实际劳动记录上的固定人群，人员变动不影响本比较'},
        'totals': totals,
        'composition': composition,
        'components': components_total,
        'component_drivers': COMPONENT_DRIVERS,
        'drivers': {
            '工时': components_total['floor'],
            '技能结构': components_total['skill'],
            '分配规则': components_total['base_rule'] + components_total['teaching'] + components_total['cap'],
        },
        'by_earning_month': earning_rows,
        'by_cash_month': cash_rows,
        'site_costs': sites,
        'people': people_rows,
        'groups': groups,
        'disclosure': {
            'min_people': min_people,
            'ratio_min_baseline_cents': ratio_min,
            'groups': disclosure_groups,
            'note': '人数低于披露下限或基数过低的群体不展示金额与比例；汇总总额仍包含这些群体，内部口径可对账',
        },
        'budget': budget,
        'reconciliation': reconciliation,
    }


def compare_schemes(store, scheme_ids, year):
    if not scheme_ids:
        raise ValueError('至少需要一个方案编号')
    rows = []
    for scheme_id in scheme_ids:
        scheme = store.get('schemes', scheme_id)
        result = simulate(store, scheme, year)
        rows.append({
            'scheme': scheme_id,
            'name': scheme.get('name', scheme_id),
            'totals': result['totals'],
            'components': result['components'],
            'drivers': result['drivers'],
            'budget': {key: result['budget'][key] for key in ('earning_need_cents', 'cash_need_cents', 'cap_cents', 'pressure_cents')},
            'disclosure': result['disclosure'],
        })
    return {'year': year, 'schemes': rows, 'note': '方案并列展示，未按任何公平定义排序或推荐'}
