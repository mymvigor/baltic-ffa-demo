import pandas as pd
import pytest

from ffa.settlement import bfa_history, daily_forecasts, forecast, sessions, shift_year, eligible_years, scenario_forecasts, subjective_adjustment
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


def test_daily_fixed_target_curves_keep_forecasts_point_in_time_and_actual_separate():
    days = pd.bdate_range('2020-01-01', '2026-12-31')
    spot = pd.DataFrame(dict(quote_day=days, price=100.+(days.year-2020)*10,
                             kind='Spot', identity='spot', tenor='spot', available_at=pd.NaT))
    dates = pd.bdate_range('2026-10-26', '2026-11-30')
    bfa = pd.DataFrame(dict(quote_day=dates, price=150., kind='BFA', identity='nov',
                            tenor='month', available_at=pd.NaT))
    quotes = pd.concat([spot, bfa], ignore_index=True)
    current = daily_forecasts(quotes, 'nov', '2026-11', '2026-11-10', [2025,2024])
    assert len(current) > 5
    assert current.Actual.isna().all()
    assert current.BFA.eq(150).all()
    changed = quotes.copy()
    changed.loc[changed.kind.eq('Spot') & changed.quote_day.gt('2026-11-10'), 'price'] = 999.
    rerun = daily_forecasts(changed, 'nov', '2026-11', '2026-12-01', [2025,2024])
    pd.testing.assert_frame_equal(current[['date','Difference','Ratio']],
        rerun.loc[rerun.date.le('2026-11-10'), ['date','Difference','Ratio']].reset_index(drop=True))
    matured = daily_forecasts(quotes, 'nov', '2026-11', '2026-12-01', [2025,2024])
    assert matured.Actual.eq(160).all()
    assert matured.date.max() <= pd.Timestamp('2026-11-30')
    assert daily_forecasts(quotes, 'nov', '2026-11', '2026-12-01', [2025], strict=True).empty
    gap = quotes[~(quotes.kind.eq('BFA') & quotes.quote_day.eq('2026-11-03'))]
    curve = daily_forecasts(gap, 'nov', '2026-11', '2026-11-10', [2025])
    assert curve.loc[curve.date.eq('2026-11-03'), 'BFA'].isna().all()


def test_bfa_basis_aligns_fixed_contracts_and_does_not_double_count_known_spot():
    days = pd.bdate_range('2020-01-01', '2026-12-31')
    spot = pd.Series(100.+(days.year-2020)*10, index=days)
    quotes = []
    for year, price in [(2024,120.),(2025,140.),(2026,180.)]:
        for month in (11,12):
            dates = pd.bdate_range(f'{year}-09-01', f'{year}-{month}-30')
            quotes.append(pd.DataFrame(dict(quote_day=dates, price=price, kind='BFA', tenor='month',
                delivery_start=pd.Timestamp(year,month,1), available_at=pd.NaT)))
    quotes = pd.concat(quotes,ignore_index=True)
    issue = pd.Timestamp('2026-11-10')
    curves = bfa_history(quotes)
    for month in ('2026-11','2026-12'):
        value = forecast(spot,issue,month,[2025,2024],5,basis='BFA',curves=curves)
        assert (value['A'],value['B'],value['C']) == (180.,130.,145.)
        assert value['values']['Difference'] == pytest.approx(195.)
        assert value['values']['Ratio'] == pytest.approx(145.*180/130)
    modified = quotes.copy()
    modified.loc[modified.quote_day.gt(issue),'price'] = 1e9
    assert forecast(spot,issue,'2026-12',[2025,2024],5,basis='BFA',curves=bfa_history(modified))['values'] == value['values']
    assert forecast(spot,issue,'2026-12',[2025],5,basis='BFA',curves=curves,strict=True) is None
    settled = forecast(spot,pd.Timestamp('2026-11-30'),'2026-11',[2025],5,basis='BFA',curves=curves)
    assert settled['values']['Difference'] == pytest.approx(160.)
    assert subjective_adjustment(settled,'Difference','Bearish',.3,10.) == 0.


def test_subjective_strength_is_explicit_and_only_changes_remaining_settlement():
    days = pd.bdate_range('2020-01-01','2026-12-31')
    spot = pd.Series(100.+(days.year-2020)*10,index=days)
    for target in ('2026-11','2026-12'):
        value = forecast(spot,pd.Timestamp('2026-11-10'),target,[2025],20)
        change = subjective_adjustment(value,'Difference','Bearish',.3,10.)
        assert change == pytest.approx(-.03*value['remaining']['Difference']*value['multiplier'])
        assert subjective_adjustment(value,'Difference','Bullish',.3,10.) == -change
        assert subjective_adjustment(value,'Difference','Neutral',.3,10.) == 0.
        with pytest.raises(ValueError):
            subjective_adjustment(value,'Difference','Bearish',1.3,10.)


def test_scenario_curve_responds_to_strength_and_keeps_observed_sessions_fixed():
    daily = pd.DataFrame({'Difference':[20000.,21000.,22000.],
                          'remaining_Difference':[20000.,24000.,22000.],
                          'remaining_weight':[1.,.5,0.], 'trend':[100.,200.,300.]})
    mild = scenario_forecasts(daily,'Difference','Bearish',.3,10.)
    strong = scenario_forecasts(daily,'Difference','Bearish',.6,10.)
    assert mild.tolist() == pytest.approx([19400.,20640.,22000.])
    assert strong.tolist() == pytest.approx([18800.,20280.,22000.])
    assert scenario_forecasts(daily,'Difference','Neutral',.6,10.).tolist() == daily.Difference.tolist()
    adjusted = scenario_forecasts(daily,'Difference','Bearish',.3,10.,('trend',),(2.,),100.)
    assert adjusted.tolist() == pytest.approx([19700.,20890.,22000.])
