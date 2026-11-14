# Baltic FFA Dashboard

Deploy main / app.py in Streamlit Community Cloud. The default uses synthetic data. Live mode uses a private GitHub repository. Configure the GitHub read credential in Streamlit Secrets.

The page has five clear sections, with one global benchmark selector and sidebar navigation:

1. **Spot Analysis:** one Spot / MA5 / MA20 trend chart alongside Bollinger bands.
2. **Price Explorer:** the sole home for fixed and rolling price histories, Spot comparisons, multi-series overlays, rebasing, spreads and ratios. Spot monthly mean remains a switch. Frequency and indicator controls update the existing chart instead of creating duplicate price, average and band charts.
3. **Term Structure:** the forward-curve snapshot and matched-delivery repricing or heatmap appear together; calendar spreads follow below.
4. **Market Relationships:** Spot / M+1 return correlation and within-contract change volatility add independent information. Percentile ranges are available directly on price and spread charts.
5. **Seasonality:** multi-year Spot and rolling-tenor overlays.

Charts remain expanded. There are no visible data tables; exact values and coverage remain on hover. The normal real-data view has 10 charts rather than the previous 19.

Seasonality compares Spot or one rolling tenor across selected calendar years (latest five by default). Fixed contract histories remain in Price Explorer. Yearly coverage and sample counts are in each line's hover details, partial periods are not extrapolated, and February 29 keeps its calendar position. Spot monthly means use stored valid daily assessments and place each point on the last observed date of the month; the latest month is month-to-date and missing months remain gaps. General period averages use available selected-window assessments, split at delivery changes; daily spreads or ratios are calculated before averaging.

Rolling prices remain unadjusted. Lines break at a contract change, and dated diamonds identify rolls. MA5, MA20 and Bollinger bands use only valid quotations within the same confirmed delivery interval; windows restart after a roll and require the full 5 or 20 observations. Display-window changes do not discard the loaded warm-up history.

Main panels refresh independently while retaining browser preferences. Archive identities are generated once per unique contract; immutable years and scoped subsets are cached by repository, credential identity and archive revision. Session views reuse merged data only while the exact live blob and archive revisions match. Same-day price revisions invalidate the workspace; transient archive failures are not cached as complete views. The live 120-second cache and market-data access are unchanged. Cross-benchmark history is loaded only for a curve comparison or an explicitly selected all-benchmark workspace.

Run `python -m pytest -q` for synthetic calculation, history-cache and Streamlit interaction checks.
