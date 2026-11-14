"""Session-local views keyed by exact live and archive revisions."""
from collections import OrderedDict
from dataclasses import dataclass

import pandas as pd

from .analytics import options
from .history import merge_history


@dataclass
class MarketView:
    fixed: pd.DataFrame
    rolling: pd.DataFrame
    days: pd.DatetimeIndex
    local: dict


class MarketWorkspace:
    def __init__(self, fixed, rolling, revision, archive_revision, max_views=8):
        self.fixed = fixed
        self.rolling = rolling
        self.revision = revision
        self.archive_revision = archive_revision
        self.max_views = max_views
        self.views = OrderedDict()

    def view(self, vessel, product, start, end, loader, *, wide=False, seasonal=False):
        key = (vessel, product, start, end, wide, seasonal)
        if key in self.views:
            self.views.move_to_end(key)
            return self.views[key]
        fixed, rolling = self.fixed, self.rolling
        if not wide:
            fixed = fixed[fixed.vessel.eq(vessel) & fixed["product"].eq(product)]
            rolling = rolling[rolling.vessel.eq(vessel) & rolling["product"].eq(product)]
        history = loader(start, end, None if wide else vessel, None if wide else product)
        cacheable = not history.attrs.get("history_unavailable", False)
        complete, relative = merge_history(fixed, rolling, history)
        current = complete[complete.vessel.eq(vessel) & complete["product"].eq(product)]
        local_rolls = relative[relative.vessel.eq(vessel) & relative["product"].eq(product)]
        # Seasonal views need no inventory of every expired fixed contract.
        eligible = current[current.kind.eq("Spot")] if seasonal else current
        result = MarketView(complete, relative, pd.DatetimeIndex(sorted(current.quote_day.unique())),
                            options(eligible, local_rolls))
        if not cacheable:
            return result
        self.views[key] = result
        while len(self.views) > self.max_views:
            self.views.popitem(last=False)
        return result
