# Baltic FFA Dashboard

Deploy main / app.py in Streamlit Community Cloud. The default uses synthetic data. Live mode uses a private GitHub repository. Configure the GitHub read credential in Streamlit Secrets.

The page has five clear sections, with one global benchmark selector and sidebar navigation:

1. **Spot Analysis:** one Spot / MA5 / MA20 trend chart, followed by Bollinger bands.
2. **Price Explorer:** the sole home for fixed and rolling price histories, Spot comparisons, multi-series overlays, rebasing, spreads and ratios. Spot monthly mean remains a switch. Frequency and indicator controls update the existing chart instead of creating duplicate price, average and band charts.
3. **Term Structure:** the forward-curve snapshot, matched-delivery repricing or heatmap, and calendar spreads appear in sequence.
4. **Market Relationships:** Spot / M+1 return correlation and within-contract change volatility add independent information. Percentile ranges are available directly on price and spread charts.
5. **Seasonality:** multi-year Spot and rolling-tenor overlays.

Charts use the full content width, appear vertically in sequence and remain expanded. There are no visible data tables; exact values and coverage remain on hover. The normal real-data view has 10 charts rather than the previous 19.

Market Relationships uses a selectable 5, 10 or 20 consecutive-change window (default 10). Each window stays within one contract; N changes require at least N+1 consecutive quotations, and correlation requires variation in both returns. Missing assessments and monthly rolls create genuine gaps. A 20-change window often yields only month-end points in monthly rolling contracts. The volatility chart is the sample standard deviation of daily absolute spread changes, in the quote unit, without annualization. These controls do not alter MA5, MA20 or Bollinger windows.

Seasonality compares Spot or one rolling tenor across selected calendar years (latest five by default). Fixed contract histories remain in Price Explorer. Yearly coverage and sample counts are in each line's hover details, partial periods are not extrapolated, and February 29 keeps its calendar position. Spot monthly means use stored valid daily assessments and place each point on the last observed date of the month; the latest month is month-to-date and missing months remain gaps. General period averages use available selected-window assessments, split at delivery changes; daily spreads or ratios are calculated before averaging.

Rolling prices remain unadjusted. Lines break at a contract change, and dated diamonds identify rolls. MA5, MA20 and Bollinger bands use only valid quotations within the same confirmed delivery interval; windows restart after a roll and require the full 5 or 20 observations. Display-window changes do not discard the loaded warm-up history.

Daily Spot price, MA and Bollinger lines join their observed points across holiday closures and missing quotes. This is a drawing rule only: no missing values are filled, and indicator calculations are unchanged. Rolling and derived contract series retain their gaps and roll breaks.

Main panels refresh independently while retaining browser preferences. Archive identities are generated once per unique contract; immutable years and scoped subsets are cached by repository, credential identity and archive revision. Session views reuse merged data only while the exact live blob and archive revisions match. Same-day price revisions invalidate the workspace; transient archive failures are not cached as complete views. The live 120-second cache and market-data access are unchanged. Cross-benchmark history is loaded only for a curve comparison or an explicitly selected all-benchmark workspace.

Run `python -m pytest -q` for synthetic calculation, history-cache and Streamlit interaction checks.

## Spot → BFA validation

Price Explorer → Spot + BFA now switches between the existing price history, daily absolute changes (USD/day), lead/lag research, and forecast backtests. The research views keep each fixed contract separate; only one benchmark/quote unit is allowed. All charts remain full-width, with no added data tables. Long training history loads only when a research view is selected and is cached by exact source revisions and scope.

The lead/lag matrix compares Spot changes at t, t−1, …, t−5 with cumulative BFA changes BFA(t+h)−BFA(t), h=1/3/5 assessment sessions. The calendar is the benchmark's union of observed assessment dates; missing quotes do not compress the horizon. Labels and features stay in one fixed contract. Filters separate months to the delivery-start month. Correlation needs at least 30 pairs; each conditional up probability needs at least 20 observations of that Spot direction, with sample counts and an unconditional up-rate comparison on hover. The 18 exploratory relationships, overlapping future labels and serial dependence preclude treating this matrix as significance or predictive proof.

**Strict PIT and exploratory date replay are separate.** Strict uses the stored vintage only after its received/available timestamp, with a prediction cutoff at 23:59:59.999999999 UTC on the assessment date. Existing historical CSVs lack publication/receipt times and vintage history: those observations are ineligible for strict validation. Exploratory replay assumes date-end availability, is prominently marked as unverified, and never confirms a usable predictive edge. No publication times are invented from quote dates.

Backtests use expanding training windows, with every training label's end date strictly before the prediction date; strict mode additionally checks label availability. The pipeline trains both feature standardization and the fixed Ridge model (alpha=10) on the training set only. Spot features are six absolute daily changes t…t−5; BFA-only has the matching six own changes. Optional peer training uses only contracts expired at that prediction time, with the same benchmark, unit, tenor and **exact integer months to delivery**. At least 60 distinct training dates and, for peer training, three peers are required. Same-day peer rows share total weight one. The selected contract's OOS score remains separate from the explicit historical-cohort validation option. Historical cohort validation deterministically selects the earliest-delivery eligible contract per date, never the contract with the best outcome.

Baselines are unchanged price, h times the current BFA change, and a BFA-only Ridge model; the richer Ridge adds Spot features. Scores use identical OOS cases, greedily select nonoverlapping target intervals and require 30 such cases. Direction has three classes (up/flat/down); unchanged price is only an error baseline. Error is mean absolute error in USD/day of the cumulative change. Incremental Spot MAE and direction gains are compared to BFA-only using paired moving-block bootstrap intervals (1,000 draws, fixed seed, block length min(20, floor(n/4)), at least two). These intervals describe uncertainty, not future guarantees. Publication uncertainty or inadequate samples blocks predictive conclusions. No hyperparameter is selected using held-out outcomes; the evaluated date window is not an untouched final holdout for future user experimentation.
