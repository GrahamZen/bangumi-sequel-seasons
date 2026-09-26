# bangumi-sequel-seasons

Bangumi 动画「续作 → 可以换成的那几季」对照表。[Izuko TV](https://github.com/GrahamZen/izuko-tv) 的首页推荐把续作换成用户没看过的最早一季时用：查表就知道换不换、换成哪一季，不用在设备上顺着前传关系一跳一个请求地往前查。

每天由 GitHub Actions 检查一次，Bangumi 数据导出（每周一次）或客户端的判据变了才重新生成。

## 取用

```
https://cdn.jsdelivr.net/gh/GrahamZen/bangumi-sequel-seasons@main/bgm-sequel-seasons.tsv
https://raw.githubusercontent.com/GrahamZen/bangumi-sequel-seasons/main/bgm-sequel-seasons.tsv
```

制表符分隔，UTF-8。首行是表头，写着判据版本、覆盖范围与所用的导出：

```
# bangumi-sequel-seasons v1 rules=1 max_id=705980 dump=dump-2026-09-22.210341Z.zip | …
```

其后每行一部有候选季的动画：

| 列 | 含义 | 例 |
|---|---|---|
| `bgm_id` | Bangumi 条目 id | `425998` |
| 候选季 | 可以换成的那几季，逗号分隔的 bgm id，播出早的在前；调用方跳过用户看过的，第一个就是要换成的那季 | `140001,278826,316247` |

- 只收有候选的（三万部动画里四千五百部左右，一百 KB 上下）。id 不超过 `max_id` 却不在表里 = 不用换；比 `max_id` 新的条目导出里还没有，表答不了，照常自己查。
- `rules` 是判据的版本：客户端只用与自己版本相同的表。

## 怎么算的

- **候选季**：从这部出发顺着「前传」关系往前走，最多 6 跳，一个条目挂着好几个前传时先走 TV/WEB 的；走过的前传里，比这部播得早、非 R18、形态是 TV/WEB 且至少 6 集的，就是候选（官方标签没写形态、没写集数的当不知道，算上），按首播日从早到晚排。按播出日期排而不是按前传链的先后：Bangumi 的「前传」是故事时间线上的，后来才做的前传会挂在第一季前面。
- **与客户端同一份代码**：走链与挑选直接编 izuko-tv 的 `walkPrequelChain` 与 `sequelSeasonCandidates`（客户端运行时回溯用的也是它们），邻居照客户端看到的 `/p1/subjects/{id}/relations` 从导出构造：只看动画、按 (order, id) 排、只取第一页 50 条；节点的首播日、集数、官方标签、R18 标记与接口里精简条目的是同一份数据。
- **Bangumi 数据**：来自官方每周的数据导出 [bangumi/Archive](https://github.com/bangumi/Archive)，整个过程不请求 Bangumi 接口。三万部动画不到一秒算完，时间主要花在下导出与构建 izuko-tv。
- **什么时候重新生成**：每天看一眼导出的 `latest.json` 与 izuko-tv 的 `SequelSeasons.kt` 里的判据版本（`SEQUEL_SEASON_RULES`），有一个变了才下导出、构建、提交；都没变几秒钟就结束。客户端改了判据会把版本加 1，所以这里一天内跟上。

## 目录

```
bgm-sequel-seasons.tsv  对照表
runner/                 出表器入口; 工作流把它拷进 izuko-tv 的测试源码里运行
scripts/                ci.sh 全部步骤 (工作流与本地共用), prepare.py 从导出取出出表器的输入
.github/workflows/      update 每日检查与更新
```

## 本地验证

改了脚本或工作流，先在 Linux (WSL 的 Ubuntu 24.04 即可, 与 `ubuntu-latest` 同版本) 里用同一份脚本跑通再推：

```
scripts/ci.sh plan     # 看要不要重出
scripts/ci.sh local    # 下载导出 → 取输入 → 取 izuko-tv → 装 JBR → 出表, 不提交
```

结果写在仓库下的 `.work/` 与 `bgm-sequel-seasons.tsv` 里，看完用 `git checkout -- bgm-sequel-seasons.tsv` 丢掉。

## 数据来源与署名

- 条目与关联数据来自 [Bangumi 番组计划](https://bgm.tv) 的官方数据导出。
