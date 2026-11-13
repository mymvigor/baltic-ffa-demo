# Baltic FFA Dashboard

Deploy main / app.py in Streamlit Community Cloud. The default uses synthetic data. Live mode uses a private GitHub repository. Configure the GitHub read credential in Streamlit Secrets.

The existing monitor now opens with standalone Spot Bollinger, MA5 and MA20 charts. Price Explorer includes a Spot monthly-mean switch. Forward Curve, Curve Changes, multi-series and paired studies, calendar spreads, correlation, percentiles and volatility remain available; analytical charts are expanded by default.

Seasonality compares Spot or one rolling tenor across selected calendar years (latest five by default). Fixed delivery contracts remain in Price Explorer and Term Structure. Each year's actual coverage is shown, partial periods are not extrapolated, and February 29 keeps its calendar position. Spot monthly means use stored valid daily assessments, show sample dates and counts, and place each point on the last observed date of the month; the latest month is month-to-date and missing months remain gaps.

Rolling prices remain unadjusted. Lines break at a contract change, and dated diamonds identify rolls. MA5, MA20 and Bollinger bands use only valid quotations within the same confirmed delivery interval; windows restart after a roll and require the full 5 or 20 observations. Display-window changes do not discard the loaded warm-up history.

Each main section refreshes independently while retaining browser preferences. Immutable historical archive years are downloaded and parsed once per cache key (repository, credential identity, revision and year), then filtered for each view. Live quote caching and market-data access remain unchanged.

Run `python -m pytest -q` for synthetic calculation, history-cache and Streamlit interaction checks.
