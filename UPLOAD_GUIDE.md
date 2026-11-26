# 本次更新的上传位置

## 2026-11-26：自动相关性、近期行情与持仓管理

本地前台包已增加默认四张 Correlation 热力图，以及独立的 Position Management。Forecast 计算和既有研究能力保留。Spot / Price Explorer 恢复默认3M时间范围；原始日价格可切换红绿 Daily Change，同页提供5D/10D/20D近期报价表。

本轮主要上传 **FFA-upload-ready.zip** 到公开前台仓库；必须连同 requirements.txt 和完整 ffa/ 目录一起更新，新增 openpyxl、tzdata，Streamlit 最低版本1.67用于按点击生成Excel。后台预测算法、本轮没有新增每日交易写入任务；私有更新包仍保留原后台分工。

Position Management 的持久保存需要在 Streamlit Secrets 增加：

```toml
positions_repo = "mymvigor/baltic-ffa-dashboard"
positions_branch = "ffa-positions"
positions_writer_token = "<仅此私有仓库 Contents 读写权限令牌>"
```

默认沿用 github_reader_token 读取；如需分开可配置 positions_reader_token。令牌只放服务端，不写入仓库。首次点击 Save to GitHub 才创建独立分支并保存交易；普通控件、Refresh、Excel导入到草稿和版本预览都不写入GitHub。并发独立记录合并，相同交易冲突则保留草稿并提示。未配置时为会话草稿；演示行情模式不会读写真实私有账户。单账户共享给获准访问看板的用户，请保持应用访问权限私有。

风险按整个账户计算，默认累计亏损$25,000，80%黄色、100%红色。支持移动加权成本、多空、部分平仓、反向及历史修改。红色或估值不完整时禁止增加敞口，减仓和平仓保留；导入批次的中间反向也不能绕过限制。此限制作用于本模拟工具，不是券商端交易限制。

Days仅适用于USD/day合约；其他单位不会被当作天数。仅对已核验日历且Spot完整的月度TC合约自动重建实际结算，未核验季度/年度或缺失数据的到期持仓显示 Settlement pending，需要补充正式结算来源和对应规则，不能拿最后报价冒充结算价。

本地真实行情副本截至2026-11-13：本轮自动相关性20项、四船型持仓及报价20项独立核算通过；另有手算多空及交易、Excel、并发冲突和针对性交互检查。没有使用真实GitHub写令牌执行线上保存/恢复，也没有上传、采集或改写真实预测记录。

本次没有执行上传或部署。两个更新包用途不同，请按包名上传，解压时保留目录结构。

1. `FFA-upload-ready.zip`：前台看板代码，上传至 **mymvigor/baltic-ffa-demo** 的 main 根目录。包含 app.py 和完整 ffa 文件夹。
2. `FFA-private-forecast-update.zip`：后台每日预测，上传至 **mymvigor/baltic-ffa-dashboard** 的 main 根目录。包含 forecast_daily.py、每日采集工作流、依赖以及后台复用的预测代码。

只上传前台包，可以看到历史走势加均值差的未来预测图、调整因子及保存浏览器记录。要在没有打开网页时也每天保存预测，还必须上传后台包。

后台沿用现有每日行情采集任务，在采集步骤后生成预测。默认取最近20个有效报价日计算当前参考均值，并选最近5个有匹配数据的历史年份；每个未来点按“往年对应合约价格＋当前参考均值－往年同期参考均值”预测。少于20个报价、缺少匹配历史合约或超过7天未更新的合约暂不自动生成，不补造历史记录。已有行情数据的存储与采集逻辑没有改写，预测放在私有 ffa-data 分支的 forecasts 目录中。

如需让自选参考窗口和主观判断也在后台每日更新，可在前台 Streamlit Secrets 另配 `github_forecast_writer_token`，仅授权私有数据仓库 Contents 读写。然后在 Forecast records 点击 **Save and enable daily updates**。不配置该令牌时，后台仍保存默认历史差值模型；交互情景保存在当前浏览器，可导出。

预测图入口：Forecast → Spot Forecast / BFA Forecast。Spot 的月度结算估计融入 Spot Forecast。复盘可切换 Historical Replay / Saved Forecasts，真实记录不会被当前参数覆盖。主观因子的作用日期固定保存，不会因后台每日运行而自动延长。

旧版水平线预测的历史记录保留原样，新预测使用 seasonal-path-v5 标记，避免把新算法冒充成过去已经生成的预测。前台、后台两个包都需更新，以保证每日任务和图表使用一致算法。

本次统一 Forecast 入口改为 Spot Forecast / BFA Forecast；不再将 Contract Settlement 作为独立主入口。Spot 预测包含有完整日历和覆盖数据的月度结算估计。前后台两个包均需更新，每日任务沿用原预测存储，为 Spot 和 BFA 分别保存。默认四项评分与复盘使用共享参数，主观情景不冒充历史成绩。多因子 Linear/Ridge 尚未接入新的共享界面，原研究实现保留。

本次修复：允许历史年份部分缺失，逐点成对计算并显示实际参与年份；固定与滚动参考年份明确区分。Forecast Evolution 使用独立目标日期，可查看尚未到期目标，不受验证期限限制。真实存档保持原值。历史评分不足10个有效非重叠样本时不显示分数，不强行得出预测优势。

时间序列图新增底部拖动时间轴，保留平移、缩放和重置；普通模型调整保留适用的图表范围。四个模块分别提供 Refresh，仅重读已存行情，不启动采集任务。Price Explorer 可搜索历史合约并跨交割年份叠加比较，低频 History 设置已收起。

真实本地行情检查：PMX Spot 与 DEC26 均生成有效预测与 Forecast Evolution；20日验证分别得到21及20个独立留出样本。该次回放的误差弱于价格不变基准，修复不会改变或美化模型成绩。历史报价缺少完整公布时间及修订版本，探索回放不能视为严格时点验证。

本轮最终版：Forecast 仅保留核心假设与一个 Prediction Horizon（默认5个报价日），自动联动目标价、涨跌幅和历史验证。Spot 默认未来3个月，BFA 默认剩余合约期限；探索情景历史分数明确属于基础模型。Spot 趋势、均线及布林线合并到一张图；时间轴保留原生双端拖动，隐藏缩略走势图。历史存档原值保留，新增引擎版本为 seasonal-path-v5。

前后台两个包需一起更新，后台会继续沿用原每日采集后的预测任务；新的自动 Spot 配置每天向前延伸3个月，不覆盖旧预测。没有执行上传、行情采集或真实每日存档任务。

准确性核对使用本地真实行情副本（2021–2026，截至2026-11-13），54项独立算术/日期检查通过。本地没有可用线上读凭据，连接器也未能读取私有存档，故不能确认线上最新数据或更早年份逐行内容。更早覆盖仅核对现有存档索引。详见本地 ACCURACY_REVIEW.md。


2026-11-26 本轮更新：第四模块改为 Correlation（三视图），Price Explorer 同一主图增加 Basis / Basis %。Forecast 月度预测结算价减当期 BFA 明确命名 Valuation Gap。原 Spot/BFA 样本外研究保留按需入口，没有新增回归或 PCA。无新增密钥或采集配置要求；没有上传、采集或改写历史预测记录。

新前台文件 ffa/series.py、ffa/basis.py、ffa/correlation.py 必须随完整包上传。不要只上传 app.py。部署后第四模块应显示 Correlation；若仍显示 Market Research，线上未加载本次代码。两个上传包已按原双仓库分工重新打包。

本轮19项真实数据独立核对通过，涵盖基差、百分比、年度交割距离匹配、隐含剩余均值、Pearson、领先方向、跨年及周频共同区间、跨船型。使用本地截至2026-11-13的行情，不代表已验证线上最新数据；相关性只是候选依据，尚未证明新增交易或预测优势。
