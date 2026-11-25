# 本次更新的上传位置

本次没有执行上传或部署。两个更新包用途不同，请按包名上传，解压时保留目录结构。

1. `FFA-upload-ready.zip`：前台看板代码，上传至 **mymvigor/baltic-ffa-demo** 的 main 根目录。包含 app.py 和完整 ffa 文件夹。
2. `FFA-private-forecast-update.zip`：后台每日预测，上传至 **mymvigor/baltic-ffa-dashboard** 的 main 根目录。包含 forecast_daily.py、每日采集工作流、依赖以及后台复用的预测代码。

只上传前台包，可以看到历史走势加均值差的未来预测图、调整因子及保存浏览器记录。要在没有打开网页时也每天保存预测，还必须上传后台包。

后台沿用现有每日行情采集任务，在采集步骤后生成预测。默认取最近20个有效报价日计算当前参考均值，并选最近5个有匹配数据的历史年份；每个未来点按“往年对应合约价格＋当前参考均值－往年同期参考均值”预测。少于20个报价、缺少匹配历史合约或超过7天未更新的合约暂不自动生成，不补造历史记录。已有行情数据的存储与采集逻辑没有改写，预测放在私有 ffa-data 分支的 forecasts 目录中。

如需让自选参考窗口和主观判断也在后台每日更新，可在前台 Streamlit Secrets 另配 `github_forecast_writer_token`，仅授权私有数据仓库 Contents 读写。然后在 Forecast records 点击 **Save and enable daily updates**。不配置该令牌时，后台仍保存默认历史差值模型；交互情景保存在当前浏览器，可导出。

预测图入口：Forecast → BFA price path。原来的最终结算价模型保留在 Contract settlement。Saved forecast review 用于回看实际生成的版本并与随后出现的真实报价比较。主观因子的作用日期固定保存，不会因后台每日运行而自动延长。

旧版水平线预测的历史记录保留原样，新预测使用 bfa-seasonal-difference-v2 标记，避免把新算法冒充成过去已经生成的预测。前台、后台两个包都需更新，以保证每日任务和图表使用一致算法。
