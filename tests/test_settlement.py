import pandas as pd
import pytest

from ffa.settlement import forecast, sessions, shift_year, eligible_years
from ffa.explorer import compare_prices


def test_basic_formulas_and_known_settlement_days():
    days = pd.bdate_range('2020-01-01', '2026-12-31')
    spot = pd.Series(100. + (days.year - 2020) * 10, index=days)
    issue = pd.Timestamp('2026-11-10')
    for month in ('2026-11', '2026-12', '2027-01'):
        result = forecast(spot, issue, month, [2025, 2024], 20)
        for name, level in [('Difference', result['C'] + result['A'] - result['B']),
                            ('Ratio', result['C'] * result['A'] / result['B'])]:
            expected = (result['fixed'] * 160 + result['left'] * level) / (result['fixed'] + result['left'])
            assert result['values'][name] == pytest.approx(expected)
        changed = spot.copy()
        changed.loc[changed.index > issue] = 1e9
        assert forecast(changed, issue, month, [2025, 2024], 20)['values'] == result['values']
    assert forecast(spot.drop(pd.Timestamp('2026-11-03')), issue, '2026-11', [2025], 20) is None


def test_calendar_and_analogue_completeness():
    assert shift_year(pd.Timestamp('2024-02-29'), 2023) == pd.Timestamp('2023-02-28')
    assert pd.Timestamp('2027-03-26') not in sessions('2027-03')
    dates = pd.bdate_range('2025-01-01','2025-12-31')
    spot = pd.Series(100., index=dates)
    assert eligible_years(spot, pd.Timestamp('2026-01-01')) == [2025]
    assert eligible_years(spot.loc[spot.index.month != 2], pd.Timestamp('2026-01-01')) == []


def test_unified_comparison_alignment_and_base_rolls():
    dates = pd.date_range('2026-01-05',periods=4)
    a = pd.DataFrame(dict(price=[100,110,120,130],segment=[1,1,1,2],roll_from=[None,None,None,'old'],
                         roll_to=[None,None,None,'new'],kind='BFA'),index=dates)
    b = pd.DataFrame(dict(price=[float('nan'),80,0,85],segment=1,roll_from=None,roll_to=None,kind='Spot'),index=dates)
    result, start = compare_prices([a,b], 'Normalized', 0)
    assert start == dates[1]
    assert all(part.price.iloc[0] == 100 for _,part in result)
    result, _ = compare_prices([a,b], 'Ratio', 1)
    assert pd.isna(result[0][1].price.loc[dates[2]])
    result, _ = compare_prices([a,b], 'Difference', 0)
    assert result[0][1].price.loc[dates[1]] == -30
    assert result[0][1].roll_from.iloc[-1] == 'old'
