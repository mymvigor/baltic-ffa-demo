"""Fixed-contract, date-purged Spot/BFA research. No adjusted rolling targets."""
import numpy as np
import pandas as pd

HORIZONS = (1, 3, 5)
SPOT_FEATURES = [f"spot_d{i}" for i in range(6)]
BFA_FEATURES = [f"bfa_d{i}" for i in range(6)]
FEATURES = BFA_FEATURES + SPOT_FEATURES
MIN_RELATION = 30
MIN_CONDITIONAL = 20
MIN_TRAIN_DATES = 60
MIN_TEST = 30
MODELS = ("Unchanged", "BFA momentum", "BFA only", "BFA + Spot")
STAGES = {"all": "全部期限", "delivery": "交割期内", "m1": "提前 1 月", "m2": "提前 2 月",
          "m3_5": "提前 3–5 月", "m6_11": "提前 6–11 月", "m12": "提前 12 月以上"}


def stage(offset):
    if offset <= 0:
        return "delivery"
    if offset == 1:
        return "m1"
    if offset == 2:
        return "m2"
    return "m3_5" if offset <= 5 else "m6_11" if offset <= 11 else "m12"


def _availability(series, index):
    values = pd.to_datetime(series.reindex(index), errors="coerce", utc=True)
    return values


def _known_window(a, b, count=7):
    arrays = [values.shift(i).astype("int64").to_numpy() for values in (a, b) for i in range(count)]
    values = np.vstack(arrays)
    missing = (values == np.iinfo(np.int64).min).any(axis=0)
    latest = values.max(axis=0)
    latest[missing] = np.iinfo(np.int64).min
    return pd.Series(pd.to_datetime(latest, utc=True), index=a.index)


def build_samples(fixed):
    """One row per fixed contract and assessment session; missing quotes stay missing.

    Session calendar is the union of observed dates for ONE benchmark/quote unit.
    t+h is indexed on that calendar, never on a compressed list of matched quotes.
    received_at conservatively dates availability of the stored vintage. Undated
    archive observations are deliberately ineligible for strict PIT validation.
    """
    if fixed.empty:
        return pd.DataFrame()
    scope = fixed[["vessel", "product", "currency", "unit"]].drop_duplicates()
    if len(scope) != 1:
        raise ValueError("Prediction samples must contain exactly one benchmark and quote unit")
    rows = fixed.loc[fixed.price.notna()].copy()
    rows["available_at"] = pd.to_datetime(rows.get("available_at", pd.Series(pd.NaT, index=rows.index)), utc=True)
    days = pd.DatetimeIndex(sorted(rows.quote_day.unique()))
    spot = rows[rows.kind.eq("Spot")].drop_duplicates("quote_day", keep="first").set_index("quote_day").reindex(days)
    if spot.price.notna().sum() < 2:
        return pd.DataFrame()
    spot_delta = spot.price.diff()
    spot_features = pd.DataFrame({f"spot_d{i}": spot_delta.shift(i) for i in range(6)}, index=days)
    spot_available = _availability(spot.available_at, days)
    batches = []
    contracts = rows[rows.kind.eq("BFA") & rows.delivery_start.notna() & rows.delivery_end.notna()]
    for identity, group in contracts.groupby("identity", sort=False):
        first = group.iloc[0]
        # Scope identity as well as delivery interval, never concatenate a rolling code.
        if group[["tenor", "delivery_start", "delivery_end"]].drop_duplicates().shape[0] != 1:
            raise ValueError("A fixed identity contains multiple delivery contracts")
        prices = group.drop_duplicates("quote_day", keep="first").set_index("quote_day").reindex(days)
        delta = prices.price.diff()
        result = spot_features.copy()
        for i in range(6):
            result[f"bfa_d{i}"] = delta.shift(i)
        result["date"] = days
        result["contract"] = identity
        result["tenor"] = first.tenor
        result["delivery_start"], result["delivery_end"] = first.delivery_start, first.delivery_end
        result["offset"] = (first.delivery_start.year - days.year) * 12 + first.delivery_start.month - days.month
        result["stage"] = result.offset.map(stage)
        result["decision_at"] = days.tz_localize("UTC") + pd.Timedelta(days=1) - pd.Timedelta(nanoseconds=1)
        availability = _availability(prices.available_at, days)
        result["features_available_at"] = _known_window(spot_available, availability)
        result["pit"] = result.features_available_at.notna() & result.features_available_at.le(result.decision_at)
        for h in HORIZONS:
            result[f"target_{h}"] = prices.price.shift(-h) - prices.price
            result[f"label_day_{h}"] = pd.Series(days, index=days).shift(-h)
            # Require the intervening assessments too: missing sessions are not skipped.
            complete_path = pd.concat([prices.price.shift(-i).notna() for i in range(h + 1)], axis=1).all(axis=1)
            result[f"target_{h}"] = result[f"target_{h}"].where(complete_path & result[f"label_day_{h}"].le(first.delivery_end))
            a, b = availability, availability.shift(-h)
            maximum = np.maximum(a.astype("int64"), b.astype("int64"))
            maximum = maximum.where(a.notna() & b.notna(), np.iinfo(np.int64).min)
            result[f"label_available_at_{h}"] = pd.to_datetime(maximum, utc=True)
        result = result.loc[prices.price.notna() & result.date.le(first.delivery_end)]
        batches.append(result)
    return pd.concat(batches, ignore_index=True) if batches else pd.DataFrame()


def select_samples(data, contract=None, selected_stage="all", strict=False, bounds=None):
    if data.empty:
        return data.copy()
    mask = pd.Series(True, index=data.index)
    if contract is not None:
        mask &= data.contract.eq(contract)
    if selected_stage != "all":
        mask &= data.stage.eq(selected_stage)
    if strict:
        mask &= data.pit
    if bounds is not None:
        mask &= data.date.between(*bounds)
    return data.loc[mask].copy()


def wilson(successes, n):
    if not n:
        return np.nan, np.nan
    p, z = successes / n, 1.96
    center = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return max(0., center - half), min(1., center + half)


def relationship(data):
    result = []
    for h in HORIZONS:
        for lag in range(6):
            pair = data[[f"spot_d{lag}", f"target_{h}", "date"]].dropna()
            x, y = pair.iloc[:, 0], pair.iloc[:, 1]
            n = len(pair)
            record = dict(horizon=h, lag=lag, n=n, dates=pair.date.nunique(), corr=np.nan,
                          up=np.nan, down=np.nan, n_up=int(x.gt(0).sum()), n_down=int(x.lt(0).sum()),
                          baseline=float(y.gt(0).mean()) if n >= MIN_RELATION else np.nan)
            if n >= MIN_RELATION and x.std() > 0 and y.std() > 0:
                record["corr"] = x.corr(y)
            for name, condition in (("up", x.gt(0)), ("down", x.lt(0))):
                count, successes = int(condition.sum()), int(y[condition].gt(0).sum())
                record[name + "_low"], record[name + "_high"] = wilson(successes, count)
                if count >= MIN_CONDITIONAL:
                    record[name] = successes / count
            result.append(record)
    return pd.DataFrame(result)


def _ridge(train, target, sample, columns, alpha=10.):
    x, y = train[columns].to_numpy(float), train[target].to_numpy(float)
    # Each calendar date has total weight 1, even with multiple peer contracts.
    weights = 1 / train.groupby("date").date.transform("size").to_numpy(float)
    mean = np.average(x, axis=0, weights=weights)
    scale = np.sqrt(np.average((x - mean) ** 2, axis=0, weights=weights))
    scale[scale < 1e-12] = 1
    x = (x - mean) / scale
    center = np.average(y, weights=weights)
    coef = np.linalg.solve(x.T @ (weights[:, None] * x) + alpha * np.eye(len(columns)),
                           x.T @ (weights * (y - center)))
    return float(center + ((sample[columns].to_numpy(float) - mean) / scale) @ coef)


def walk_forward(data, contract, horizon, *, pooled=False, historical=False, selected_stage="all", bounds=None,
                 strict=True, min_train=MIN_TRAIN_DATES):
    """Expanding, date-purged predictions; every row is refitted from past labels.

    Peer contracts must be expired at prediction time and match tenor AND exact
    months-to-delivery. Targets of the selected contract are never spliced across
    rolls. Test scope is explicit; pooled historical results are not its own score.
    """
    if data.empty:
        return pd.DataFrame()
    chosen = data[data.contract.eq(contract)]
    if chosen.empty:
        return pd.DataFrame()
    universe = data[data.tenor.eq(chosen.tenor.iloc[0])].dropna(subset=FEATURES).copy()
    if strict:
        universe = universe.loc[universe.pit]
    tests = select_samples(universe, None if historical else contract, selected_stage, bounds=bounds)
    target, label_day, label_at = f"target_{horizon}", f"label_day_{horizon}", f"label_available_at_{horizon}"
    tests = tests.dropna(subset=[target]).sort_values(["date", "offset", "contract"])
    if historical:
        # One deterministic nearest-delivery contract per date; no cross-sectional
        # multiplication of the same Spot signal in reported validation samples.
        tests = tests.drop_duplicates("date", keep="first")
    results = []
    for _, sample in tests.iterrows():
        train = universe.loc[universe[label_day].lt(sample.date) & universe.offset.eq(sample.offset)
                             & universe[target].notna()]
        own = train.contract.eq(sample.contract)
        allowed = own | (train.delivery_end.lt(sample.date) if pooled else False)
        train = train.loc[allowed]
        if strict:
            train = train.loc[train[label_at].notna() & train[label_at].le(sample.decision_at)]
        if train.date.nunique() < min_train:
            continue
        peers = train.loc[train.contract.ne(sample.contract), "contract"].nunique()
        if pooled and peers < 3:
            continue
        record = dict(date=sample.date, contract=sample.contract, offset=sample.offset,
                      label_day=sample[label_day], actual=sample[target],
                      train_dates=train.date.nunique(), train_contracts=train.contract.nunique(),
                      train_last_label=train[label_day].max(), train_latest_available=train[label_at].max(),
                      train_peer_latest_end=train.loc[~train.contract.eq(sample.contract), "delivery_end"].max(),
                      train_offset_min=train.offset.min(), train_offset_max=train.offset.max(),
                      decision_at=sample.decision_at)
        record["Unchanged"] = 0.
        record["BFA momentum"] = float(horizon * sample.bfa_d0)
        record["BFA only"] = _ridge(train, target, sample, BFA_FEATURES)
        record["BFA + Spot"] = _ridge(train, target, sample, FEATURES)
        results.append(record)
    return pd.DataFrame(results)


def independent_forecasts(predictions):
    if predictions.empty:
        return predictions.copy()
    chosen, next_day = [], pd.Timestamp.min
    for index, row in predictions.sort_values(["date", "offset", "contract"]).iterrows():
        if row.date >= next_day:
            chosen.append(index)
            next_day = row.label_day
    return predictions.loc[chosen].sort_values("date").copy()


def score(predictions, min_test=MIN_TEST):
    independent = independent_forecasts(predictions)
    if len(independent) < min_test:
        return independent, pd.DataFrame(), {}
    actual = independent.actual.to_numpy(float)
    metrics = []
    for name in MODELS:
        predicted = independent[name].to_numpy(float)
        hits = np.sign(predicted) == np.sign(actual)
        low, high = wilson(int(hits.sum()), len(hits))
        metrics.append(dict(model=name, mae=float(np.abs(predicted - actual).mean()),
                            accuracy=float(hits.mean()) if name != "Unchanged" else np.nan,
                            low=low, high=high))
    # Paired moving-block bootstrap preserves local dependence after labels no
    # longer overlap. This is uncertainty, not a claim of independent daily draws.
    loss_delta = np.abs(independent["BFA only"] - actual) - np.abs(independent["BFA + Spot"] - actual)
    hit_delta = ((np.sign(independent["BFA + Spot"]) == np.sign(actual)).astype(float)
                 - (np.sign(independent["BFA only"]) == np.sign(actual)).astype(float))
    values = np.column_stack([loss_delta, hit_delta])
    n, block = len(values), max(2, min(20, len(values) // 4))
    rng = np.random.default_rng(2026)
    starts = rng.integers(0, n - block + 1, size=(1000, int(np.ceil(n / block))))
    indices = (starts[:, :, None] + np.arange(block)).reshape(1000, -1)[:, :n]
    draws = values[indices].mean(axis=1)
    interval = np.quantile(draws, [.025, .975], axis=0)
    extra = dict(mae_gain=float(loss_delta.mean()), accuracy_gain=float(hit_delta.mean()),
                 mae_interval=interval[:, 0].tolist(), accuracy_interval=interval[:, 1].tolist(), block=block)
    return independent, pd.DataFrame(metrics), extra
