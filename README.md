# Baltic FFA Dashboard

Deploy main / app.py in Streamlit Community Cloud. The default uses synthetic data. Live mode uses a private GitHub repository. Configure the GitHub read credential in Streamlit Secrets.

All four modules appear on one page, with global vessel and benchmark selection:

1. **Forecast:** monthly settlement Difference and Ratio models, independently configured exploratory corrections and chronological validation. The current forecast chart appears by default; historical charts calculate on demand.
2. **Spot Market:** Spot / MA5 / MA20, Bollinger bands and Spot seasonality.
3. **Forward Market:** one Price Explorer for prices, normalization, differences, ratios and calendar overlays, plus Forward Curve and Repricing views.
4. **Market Research:** daily changes, lead/lag, short-horizon BFA validation and relationships.

Charts use the full content width without data tables. Selected views load on demand.

Settlement models show both equations and their intermediate values. Published settlement sessions are locked when the verified calendar is available. Quarterly and annual strips are shown by monthly settlement leg. Current dry-index calendars cover 2026 and 2027. Earlier reconstructed actuals are not reconciled to clearinghouse prints. Reference-window comparisons use the early development interval; the recent 30% is reserved for holdout scoring. Fitted exploratory corrections use earlier matured targets and reject highly collinear inputs. Manual factors are subjective scenarios.

Configurations and generated current forecasts are saved in the current browser and can be exported. Records include their actual creation time. Replay dates do not generate historical forecast records. Background daily generation and cross-device journal storage have not yet been connected to the private collector.

Settlement rules: https://www.balticexchange.com/en/FFAS/How-to-trade-an-FFA/Part-four-Settlement.html
Publication calendars: https://www.balticexchange.com/en/data-services/market-information0/holidays---non-publications.html

Market Relationships uses a selectable 5, 10 or 20 consecutive-change window (default 10). Each window stays within one contract; N changes require at least N+1 consecutive quotations, and correlation requires variation in both returns. Missing assessments and monthly rolls create genuine gaps. A 20-change window often yields only month-end points in monthly rolling contracts. The volatility chart is the sample standard deviation of daily absolute spread changes, in the quote unit, without annualization. These controls do not alter MA5, MA20 or Bollinger windows.

Seasonality compares Spot or one rolling tenor across selected calendar years (latest five by default). Fixed contract histories remain in Price Explorer. Yearly coverage and sample counts are in each line's hover details, partial periods are not extrapolated, and February 29 keeps its calendar position. Spot monthly means use stored valid daily assessments and place each point on the last observed date of the month; the latest month is month-to-date and missing months remain gaps. General period averages use available selected-window assessments, split at delivery changes; daily spreads or ratios are calculated before averaging.

Rolling prices remain unadjusted. Lines break at a contract change, and dated diamonds identify rolls. MA5, MA20 and Bollinger bands use only valid quotations within the same confirmed delivery interval; windows restart after a roll and require the full 5 or 20 observations. Display-window changes do not discard the loaded warm-up history.

Daily Spot price, MA and Bollinger lines join their observed points across holiday closures and missing quotes. This is a drawing rule only: no missing values are filled, and indicator calculations are unchanged. Rolling and derived contract series retain their gaps and roll breaks.

Main panels refresh independently while retaining browser preferences. Archive identities are generated once per unique contract; immutable years and scoped subsets are cached by repository, credential identity and archive revision. Session views reuse merged data only while the exact live blob and archive revisions match. Same-day price revisions invalidate the workspace; transient archive failures are not cached as complete views. The live 120-second cache and market-data access are unchanged. Cross-benchmark history is loaded only for a curve comparison or an explicitly selected all-benchmark workspace.

Run `python -m pytest -q` for synthetic calculation, history-cache and Streamlit interaction checks.

## Spot → BFA validation

Market Research switches between daily absolute changes (USD/day), lead/lag research, forecast backtests and relationships. The research views keep each fixed contract separate; only one benchmark/quote unit is allowed. All charts remain full-width, with no added data tables. Long research training history loads only when required and is cached by exact source revisions and scope.

Research defaults to clearly labelled **Date replay**, **Historical peers**, **M+1** and a **3Y** window, so the initial view has useful validation coverage. Select **Selected contract** to inspect one fixed delivery separately. **Verified timestamps** and contract type are available in **Research settings**; methodology is under **Method notes**. Price-history defaults are unchanged. Small-sample thresholds and leakage controls are unchanged.

The research window loads two years of initial training history before the selected validation start, plus the existing calendar warmup, and then expands training forward. It does not load the entire old archive for the default view. Data remain continuous through the latest saved assessment so future-session labels never skip an unloaded calendar interval. Select All history to include the entire available archive.

The lead/lag matrix compares Spot changes at t, t−1, …, t−5 with cumulative BFA changes BFA(t+h)−BFA(t), h=1/3/5 assessment sessions. The calendar is the benchmark's union of observed assessment dates; missing quotes do not compress the horizon. Labels and features stay in one fixed contract. Filters separate months to the delivery-start month. Correlation needs at least 30 pairs; each conditional up probability needs at least 20 observations of that Spot direction, with sample counts and an unconditional up-rate comparison on hover. The 18 exploratory relationships, overlapping future labels and serial dependence preclude treating this matrix as significance or predictive proof.

**Strict PIT and exploratory date replay are separate.** Strict uses the stored vintage only after its received/available timestamp, with a prediction cutoff at 23:59:59.999999999 UTC on the assessment date. Existing historical CSVs lack publication/receipt times and vintage history: those observations are ineligible for strict validation. Exploratory replay assumes date-end availability, is prominently marked as unverified, and never confirms a usable predictive edge. No publication times are invented from quote dates.

Backtests use expanding training windows, with every training label's end date strictly before the prediction date; strict mode additionally checks label availability. The pipeline trains both feature standardization and the fixed Ridge model (alpha=10) on the training set only. Spot features are six absolute daily changes t…t−5; BFA-only has the matching six own changes. Peer training uses only contracts expired at that prediction time, with the same benchmark, unit, tenor and **exact integer months to delivery**. At least 60 distinct training dates and three peers are required. Same-day peer rows share total weight one. The selected contract's OOS score remains separate from historical-cohort validation. Historical cohort validation deterministically selects the earliest-delivery eligible contract per date, never the contract with the best outcome.

Baselines are unchanged price, h times the current BFA change, and a BFA-only Ridge model; the richer Ridge adds Spot features. Scores use identical OOS cases, greedily select nonoverlapping target intervals and require 30 such cases. Direction has three classes (up/flat/down); unchanged price is only an error baseline. Error is mean absolute error in USD/day of the cumulative change. Incremental Spot MAE and direction gains are compared to BFA-only using paired moving-block bootstrap intervals (1,000 draws, fixed seed, block length min(20, floor(n/4)), at least two). These intervals describe uncertainty, not future guarantees. Publication uncertainty or inadequate samples blocks predictive conclusions. No hyperparameter is selected using held-out outcomes; the evaluated date window is not an untouched final holdout for future user experimentation.
