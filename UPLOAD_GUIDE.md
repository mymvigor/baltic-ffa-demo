# Position Management 更新与部署

按最新要求：**填写后点击 Save changes 才整批提交；不进行10秒自动提交。**

本轮小幅调整：Trade Entry初始一行，点击Add row每次增加一个空行；支持继续批量粘贴，空行不会计为成交。录入表各列完整显示，窄屏自动换行，不再使用左右滚动。合约按未到期优先、月度优先、最新报价及近期交割排序，历史合约保留搜索。

指定12笔成交的原始来源、Excel备份和核对结果现已直接放入本地私有项目 `migrations/position_b_20261125/`，不是仅在部署压缩包里；公开前台包不包含这些交易。按用户本轮选择，先完成本地修改，不执行线上写入。

前台保持单页：Account Overview → Open Positions → Trade Entry → P&L Performance → Trade History。默认整个账户。日期、合约和船型筛选按需打开，风险仍按整个账户判断。

Trade Entry支持连续录入、Excel多行粘贴、搜索Fixed合约；日期默认当天，Quantity首次5 days，随后记忆有效输入。USD/day按days计量时盈亏乘数为1，不再乘月份天数；未核验其他数量单位或乘数的合约不冒算盈亏。

仅保留一个Save changes。完整批次经过逐笔风险检查后写入私有GitHub；成功确认后才更新正式持仓。历史修改和经确认的删除随批次保存。未完成或校验失败的批次不写入，也不计入正式盈亏。已点击保存但网络失败的原批次可自动重试；继续修改后需要重新点击Save changes。

待保存记录保存在当前浏览器localStorage；不依赖关闭网页事件。保存确认后清理对应草稿。浏览器禁用存储、清理网站数据或无痕窗口结束会失去本地草稿，GitHub正式台账不受影响。同一浏览器同时打开多个标签时使用本地草稿租约避免互相覆盖。并发独立成交可合并，同一笔成交的冲突保留待处理内容，不静默覆盖；可用Undo撤回冲突行，再根据最新流水修改。

## 前台部署

将FFA-upload-ready.zip解压到公开前台仓库根目录，保留完整ffa目录和requirements.txt。仅前台代码，不包含个人成交、Excel备份、迁移源数据或令牌。

Streamlit Secrets配置：

```toml
positions_repo = "mymvigor/baltic-ffa-dashboard"
positions_branch = "ffa-positions"
positions_writer_token = "<私有仓库 Contents read/write 令牌>"
# 读取默认沿用github_reader_token，也可另设positions_reader_token。
```

服务端没有写令牌时，页面保留浏览器草稿并说明未配置权限，不伪报保存成功。演示行情不会读取或写入真实台账。保持应用访问权限私有。

## 附件历史迁移：仅限私有仓库

FFA-private-position-migration.zip只解压到 **mymvigor/baltic-ffa-dashboard** 的默认代码分支，绝不可上传公开前台仓库。该包包含原始Excel备份、已核对的源记录、独立核算报告、一次性迁移脚本和私有Actions工作流。

上传该私有更新后，Migrate private position history工作流自动执行，不需要在前台再次导入。它使用该私有仓库的GITHUB_TOKEN，并要求Actions允许Contents读写。若Actions禁止自动运行，可在同一工作流使用Run workflow重试；稳定源行ID与迁移标记保证幂等。此任务不采集行情、不修改行情和预测存档。

迁移只导入指定交易员的原始成交字段，保留源行顺序；相同成交的不同源行仍是不同成交。既有交易保留。迁移属于历史事实记录，不因当前风险阈值删减。正式盈亏只用现有BFA动态计算，不导入Excel自动计算的盈亏。完成后工作流重新读取私有台账，核对全部迁移ID与标记后才报告成功。

当前本地未配置私有仓库写凭据，连接器也未获该仓库读取权限，故**尚未实际写入正式GitHub台账**。迁移包中的核对报告明确区分附件报价核对和较旧本地行情估值；不把附件报价静默覆盖进原行情源。实际线上报价的最终核对须在私有工作流运行时完成。

## 检查范围

针对性检查覆盖既有移动加权成本、部分平仓、反向、整批保存、无效批次不写、网络失败、响应丢失幂等、重新读取持久化、历史修改删除、并发冲突、逐笔风险和一次性迁移。客户端逻辑检查确认没有10秒提交，粘贴重复成交不丢失，草稿可恢复，只有显式Save或已授权失败批次重试才提交。

原生Edge端到端测试在本机启动通信阶段超时，未冒充已完成完整浏览器验收；已完成组件逻辑与Streamlit服务端针对性检查。Forecast、Spot、Forward及Correlation源代码未因本轮持仓调整改变。
