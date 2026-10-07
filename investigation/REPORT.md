# PKU Know Phase 0 API investigation

调查日期：2026-10-07，北京时间。API 采样为 13:18:12–13:20:04，共 12 次成功的 GET，串行执行、请求之间至少等待 3 秒；另读取公开首页和两个公开 JS 文件。未登录、未访问 sources 中列出的内部资源、未携带 Cookie/Authorization。User-Agent：`PKURadar-Phase0/0.1 (read-only public API investigation)`。

范围：仅行为调查与离线证据；没有实现业务模块。网络工具首次访问首页失败，Python TLS 客户端也遇到证书校验错误，随后使用系统 curl 正常访问，未关闭证书验证。请求 URL、UTC 时间、状态码和响应大小见 `requests.json`；下文时间均为北京时间。

## Observed API behavior

实际 endpoint：`GET https://pkuknow.cn/api/notices`。

比较基线：`q=&category=全部通知&source=all&group=wechat,official&intent=activity`，仅改变表中 view/page。

| 请求 | items / unique ids | 响应 page / page_size | total | published_at 范围（2026 年） | 全局降序 |
|---|---:|---:|---:|---|---|
| list, page=1 | 30 / 30 | 1 / 30 | 1373 | 10-06 14:39:47 至 10-07 12:05:46 | 是 |
| list, page=2 | 30 / 30 | 2 / 30 | 1373 | 10-04 11:33:09 至 10-06 13:47:28 | 是 |
| list, page=3 | 30 / 30 | 3 / 30 | 1373 | 10-02 19:30:00 至 10-04 10:00:00 | 是 |
| board, page=1 | 120 / 120 | 1 / 30 | 1373 | 09-18 12:12:39 至 10-07 12:05:46 | 否 |
| board, page=2 | 120 / 120 | 1 / 30 | 1373 | 同上 | 否 |

- list 1/2、1/3、2/3 的 ID 交集均为 **0**，合并后 90 条也为发布时间非递增顺序。相同时间戳的次级排序规则、数据更新时的跨页一致性均为 **unknown**。
- board 两次响应逐字节一致，ID 和顺序均 100% 重复；六个 activity 分类各取 20 条，并交错排列，全局有 69 处相邻发布时间上升。它是分类预览窗口，不能按普通分页遍历，也不能用 `page_size=30` 推算实际返回条数。
- list 第 1/2/3 页与 board 的 ID 交集分别是 30/24/10；board 虽有 120 条，却遗漏 list 前 90 条中的 26 条。
- 响应确有 `total`, `page`, `page_size`；没有 `next_page`、`has_more` 或 cursor。list 前端以 `page * page_size >= total` 判断最后一页。`total_all=2245` 与当前筛选结果 `total` 不同。
- 其他顶层字段完整保留：`items`, `sources`, `groups`, `intent_totals`, `category_totals`, `view`, `last_sync`, `last_attempt`, `interval_minutes`。

### 前端实际取数方式

公开首页在 HTML 中提供 initialData。公开脚本 `/_next/static/chunks/6745-8a79b024ccee7aa7.js`（本地 `frontend-notices.js`）构造上述七个参数并调用同一个 `/api/notices`。

`category === 全部通知` 时前端选择 board；分类卡片的“查看更多”进入分类路由，分类列表使用 list，并通过上一页/下一页改变 page。首页不是无限追加 board 页。脚本还包含页面可见时每 60 秒刷新、切换回页面时刷新；本调查没有照此轮询。直接 API 请求 `category=全部通知&view=list` 也有效，适合近期顺序读取。没有必要寻找另一个 endpoint；本阶段未做大范围 endpoint 枚举。

### 五个参数的实际效果

以下每次仅相对 list/page=1 基线改变一个参数，全部 HTTP 200：

| 参数变更 | total | 返回条数 / 与基线 ID 交集 | 结果 |
|---|---:|---:|---|
| intent=all | 2245 | 30 / 19 | 19 activity + 11 information，筛选有效 |
| category=讲座活动 | 395 | 30 / 6 | 全部为讲座活动，筛选有效 |
| group=official | 639 | 30 / 0 | 全部属于 sources 标注的 official 源，筛选有效 |
| source=weixinzs_422816432 | 8 | 8 / 1 | 全部来自该 source_id，筛选有效 |
| view=board | 1373 | 120 / 30 | 改为分类预览窗口，行为有效 |

这只能确认测试值，不能推断无效参数、所有参数组合或默认值的行为。四次筛选变更的 `sources` 与基线完全相同，仍为 205 个来源；健康块不是当前 items 的来源子集。

额外检查 `intent=all&view=list&page=1/2/3`：共 90 个唯一 ID，无跨页重复，合并按 published_at 降序，覆盖 10-04 18:52:59 至 10-07 12:05:46。三个页面的 total 均为 2245、page_size 均为 30。

## Data observations

### 字段、identity、活动标签

items 使用用户列出的 12 个字段。全体 12 份响应共观察到 **231 个唯一 ID**，没有同 ID 不同 item 内容；所有 ID 都以 `source_id + ':'` 开头，后缀有数字和不透明字符串。短期内适合作为 PKU Know source-local identity；应保留整个 ID，并以 `(provider=pkuknow, id)` 去重。跨来源同内容不会自动合并，长期稳定性、重建后是否换 ID 为 **unknown**。

activity/list 前三页的 90 条均来自微信来源，因此以下缺失率不能代表所有来源：

| 字段 | key 缺失 | null | 空字符串 |
|---|---:|---:|---:|
| ai_summary | 0 | 0 | 0 |
| ai_is_event | 0 | 0 | 0 |
| ai_event_time | 0 | 49（54.4%） | 0 |
| ai_event_location | 0 | 51（56.7%） | 0 |

ai_is_event：true 60，false 30。结合标题和摘要，至少 **30/90（33.3%）** 是招聘/实习申请内容，并非严格的定时活动；这是一项保守的样本判断，不是总体准确率或完整人工标注。例：`weixinzs_422914528:12176460` 为长期人才招聘，`weixinzs_422820418:12080509` 为暑期实习网申。部分招聘仍标 true，不能把 AI bool 当事实标准。时间字段是自然语言文本，有仅月日、多个日期或“暂定”等形式。

all/list 前 90 条中 ai_is_event 为 true 57 / false 33；时间 null 51、地点 null 54。应允许 AI 字段为空并保留上游值。

### sources / health

基线有 205 个来源：153 wechat、52 official。

| status | 数量 |
|---|---:|
| ok | 166 |
| no_recent | 24（wechat 22 / official 2） |
| failed | 10 |
| temporarily_closed | 3 |
| requires_key | 1 |
| directory | 1 |

- 13 个来源 `last_success=null`，即响应中没有成功记录；**不能证明它们历史上从未成功**。其中 failed 7、temporarily_closed 3、requires_key 1、directory 1、no_recent 1。
- failed 中还有 3 个有历史成功时间且保留旧数据，例如 `bio_students`、`info`；因此非空 items/count 也不代表本轮同步成功。
- `career`、`cc_notices`、`nsd_forums` 虽 status=ok，`detail_failures` 分别为 2、1、1，表明详情覆盖不完整。其他来源可能不含此字段，不能把缺失当作显式 0。
- `no_recent` 不等于失败，也不等于已证明没有新内容：官方源可能报“当前列表未发现近期条目”，微信源可能报“等待服务商收录文章”。部分 no_recent 的 count 仍非零。
- 微信 153 个源均标 `coverage=provider_archive`，上游明确提示历史完整性取决于服务商覆盖。
- 有用字段：源级 `status`, `message`, `last_success`, `checked_at`, `latest_at`, `detail_failures`, `coverage`, `count`；全局 `last_sync`, `last_attempt`, `interval_minutes`。源级未发现 `last_sync`。
- 初次全局 last_sync/last_attempt 均为 13:15，interval_minutes=5。重复时 last_attempt 已变为 13:20，但 last_sync 和所有 sources 均未变。这些时间戳不能单独证明每个源都已成功更新。官方源 checked_at 多为当天 08:00，不能对所有来源简单套用全局 5 分钟刷新期限。

### Late-arriving items

13:20:04 重复 13:18:12 的 activity/list/page=1 请求，间隔约 112 秒，items 及顺序完全一致，新增 ID=0；仅顶层 last_attempt 改变。

是否有较旧 published_at 的条目在后续抓取中才出现：**unknown**。本次未观察到，不代表不会发生；没有 ingestion 时间或稳定增量 cursor 的证据。published_at 不能当严格增量游标。分页在更新过程中是否会漂移也为 unknown。

## Recommended MVP fetch strategy

推荐 **fixed recent window + persistent id dedup**，使用 list。为覆盖“近期校园信息”而不在采集阶段丢弃事务/资讯，建议固定：

`GET /api/notices?q=&category=全部通知&source=all&group=wechat,official&intent=all&view=list&page=N`

1. 每日串行读取 **page 1–3，当前最多 90 条**，请求间留 3 秒。只在响应确认已到尾页时提前结束。这是基于当前约 2.7 天样本窗口的初始预算，不是高峰期或长延迟下的完整性保证；节假日采样不能估计正常教学日流量。若产品明确仅取 activity，可保留 activity，当前 90 条覆盖约 4.7 天，但它仍不等于严格活动。
2. 每次读完固定窗口，对所有 ID 做持久去重。**遇到已见 ID 或整页已见也不停止**；迟到条目可能位于更后位置。保存本地 first_seen 时间供以后评估迟到情况，本阶段不实现。
3. **published_at 仅用于排序、展示和窗口覆盖诊断，不用于排除未见 ID、提前停止或严格 cutoff。** 不以“早于上次运行”丢弃条目。
4. HTTP/JSON/schema 错误、任一计划页面失败、异常页号/重复整页，标记本次读取 partial。如果第三页最旧时间仍晚于上次成功运行时间，说明预算连名义时间跨度都未覆盖，应标记 window coverage insufficient，供 review 后增加预算；即使跨过该时间也不证明无遗漏。上游变化时 offset 分页缺少快照保障。
5. 对纳入产品范围的源：failed、缺少成功记录、detail_failures>0、requires_auth/requires_key、暂时关闭、成功时间持续停滞等，记录 degraded/partial 原因。`directory` 是静态目录，宜作为明确覆盖排除项。没有足够证据制定每个源的精确 freshness SLA；先保留逐源时间与变化趋势，不从 interval_minutes 推导统一阈值。
6. no_recent 单独记录，不自动视为故障；若同时缺少成功记录、长期停滞或出现覆盖下降，应报告不确定/退化。全局 last_attempt 前进但 last_sync 不前进只能作为辅助信号。此次已有失败源和详情失败，当前上游覆盖应标记 **partial/degraded**，即使 API 全部 HTTP 200。

预算外的超长迟到、爆量或跨页更新仍可能漏读；此次无法证明无损增量。这个有限窗口方案比 board 更直接、有实测依据，之后只需根据实际日流量和窗口跨度调整预算，无需本阶段追求历史全量遍历。

## Assumptions rejected

- board 的 page 是稳定普通分页：不成立，1/2 页逐字节相同。
- page_size 必然等于 items 长度：board 中为 30 vs 120。
- board 条数更多即可覆盖同等数量的最新通知：不成立，漏掉 list 前 90 条中的 26 条。
- intent/category/group/source/view 只是 UI 参数：不成立，测试值都改变了响应行为。
- activity 等于严格活动、AI 时间地点总是完整：不成立。
- HTTP 200、全局同步时间或非空数据证明所有源健康：不成立。
- sources 自动随当前筛选缩小：不成立。
- 已见 ID 可以安全提前停止、published_at 可作严格增量 cutoff：没有证据支持，拒绝作为 MVP 前提；迟到行为仍为 unknown。

## Saved fixture

- `tests/fixtures/pkuknow_notices.json`：原样完整保存 activity/list/page=1 的 HTTP response body，104697 bytes；含 30 items、分页元数据、205 sources、统计与健康元数据。没有剥离字段，没有存储请求凭据或 Cookie。
- SHA-256：`4a3722f751e3d37b31a35b4246e55c77251f1bfb1c9acaa63d4411ff6292b797`。
- `investigation/*.json` 保留 12 份原始响应及请求记录，支持离线复查分页、参数差异和重复请求；公开脚本保留前端取数证据。`probe*.py` 仅为本次调查采样脚本，未接入业务或调度。
- 离线验证：全部 12 份响应可解析；逐页 ID 交集、排序、字段缺失和健康计数已计算；fixture 与原响应逐字节一致。未运行业务测试，因为本次没有业务代码改动。

调查完成，停止并等待 review。
