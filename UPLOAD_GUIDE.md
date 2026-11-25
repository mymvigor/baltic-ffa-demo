# 本次更新的上传位置

本次没有执行上传或部署。两个更新包用途不同，请按包名上传，解压时保留目录结构。

1. `FFA-upload-ready.zip`：前台看板代码，上传至 **mymvigor/baltic-ffa-demo** 的 main 根目录。包含 app.py 和完整 ffa 文件夹。
2. `FFA-private-forecast-update.zip`：后台每日预测，上传至 **mymvigor/baltic-ffa-dashboard** 的 main 根目录。包含 forecast_daily.py、每日采集工作流、依赖以及后台复用的预测代码。

只上传前台包，可以看到历史走势加均值差的未来预测图、调整因子及保存浏览器记录。要在没有打开网页时也每天保存预测，还必须上传后台包。

后台沿用现有每日行情采集任务，在采集步骤后生成预测。默认取最近20个有效报价日计算当前参考均值，并选最近5个有匹配数据的历史年份；每个未来点按“往年对应合约价格＋当前参考均值－往年同期参考均值”预测。少于20个报价、缺少匹配历史合约或超过7天未更新的合约暂不自动生成，不补造历史记录。已有行情数据的存储与采集逻辑没有改写，预测放在私有 ffa-data 分支的 forecasts 目录中。

如需让自选参考窗口和主观判断也在后台每日更新，可在前台 Streamlit Secrets 另配 `github_forecast_writer_token`，仅授权私有数据仓库 Contents 读写。然后在 Forecast records 点击 **Save and enable daily updates**。不配置该令牌时，后台仍保存默认历史差值模型；交互情景保存在当前浏览器，可导出。

预测图入口：Forecast → Spot Forecast / BFA Forecast。Spot 的月度结算估计融入 Spot Forecast。复盘可切换 Historical Replay / Saved Forecasts，真实记录不会被当前参数覆盖。主观因子的作用日期固定保存，不会因后台每日运行而自动延长。

旧版水平线预测的历史记录保留原样，新预测使用 seasonal-path-v4 标记，避免把新算法冒充成过去已经生成的预测。前台、后台两个包都需更新，以保证每日任务和图表使用一致算法。

本次统一 Forecast 入口改为 Spot Forecast / BFA Forecast；不再将 Contract Settlement 作为独立主入口。Spot 预测包含有完整日历和覆盖数据的月度结算估计。前后台两个包均需更新，每日任务沿用原预测存储，为 Spot 和 BFA 分别保存。默认四项评分与复盘使用共享参数，主观情景不冒充历史成绩。多因子 Linear/Ridge 尚未接入新的共享界面，原研究实现保留。

本次修复：允许历史年份部分缺失，逐点成对计算并显示实际参与年份；固定与滚动参考年份明确区分。Forecast Evolution 使用独立目标日期，可查看尚未到期目标，不受验证期限限制。真实存档保持原值。历史评分不足10例时明确标为低可信度，不强行得出预测优势。

时间序列图新增底部拖动时间轴，保留平移、缩放和重置；普通模型调整保留适用的图表范围。四个模块分别提供 Refresh，仅重读已存行情，不启动采集任务。Price Explorer 可搜索历史合约并跨交割年份叠加比较，低频 History 设置已收起。

真实本地行情检查：PMX Spot 与 DEC26 均生成有效预测与 Forecast Evolution；20日验证分别得到21及20个独立留出样本。该次回放的误差弱于价格不变基准，修复不会改变或美化模型成绩。历史报价缺少完整公布时间及修订版本，探索回放不能视为严格时点验证。
