/*
 * bangumi-sequel-seasons 的出表器. 由 bangumi-sequel-seasons 仓库的工作流拷进 izuko-tv 的
 * app/shared/app-data/src/desktopTest/kotlin/data/network/ 再运行, 不属于 app 仓库.
 */

package me.him188.ani.app.data.network

import kotlinx.coroutines.runBlocking
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import me.him188.ani.app.data.recommendation.MAX_PREQUEL_HOPS
import me.him188.ani.app.data.recommendation.SEQUEL_SEASON_RULES
import me.him188.ani.app.data.recommendation.isSeasonFormat
import me.him188.ani.app.data.recommendation.sequelSeasonCandidates
import me.him188.ani.datasources.api.PackedDate
import java.io.File
import kotlin.test.Test

/**
 * 给推荐的续作换季出一张表: 每部动画能换成的那几季 (按挑选顺序), 只收有候选的.
 *
 * **走链与挑选都是 app 自己的代码** ([walkPrequelChain] + [sequelSeasonCandidates]), 与电视上运行时回溯同一份;
 * 喂给它的邻居来自 Bangumi 数据导出 (prepare.py 精简出的 anime.jsonl / relations.jsonl), 照 app 看到的
 * `/p1/subjects/{id}/relations` 构造: 只看动画、按 (order, id) 排、只取第一页 ([SERIES_RELATIONS_PAGE_SIZE] 条);
 * 节点的首播日、集数、官方标签、nsfw 与接口里精简条目的是同一份数据.
 *
 * 不发任何请求, 三万部一两秒算完.
 *
 * 环境变量:
 * - `BGM_SEQUEL_WORK`: prepare.py 的输出目录 (anime.jsonl / relations.jsonl / dump.name); 不设就跳过
 * - `BGM_SEQUEL_OUT`: 输出的表
 */
class BgmSequelSeasonsRunner {
    @Serializable
    private data class Anime(
        val id: Int,
        val name: String,
        val nameCN: String = "",
        val date: String = "",
        val eps: Int = 0,
        val nsfw: Boolean = false,
        val meta: List<String> = emptyList(),
    )

    @Serializable
    private data class Rel(val id: Int, val rel: List<List<Int>>)

    @Test
    fun run() {
        val workDir = File(System.getenv("BGM_SEQUEL_WORK") ?: run {
            println("跳过: 设 BGM_SEQUEL_WORK 才跑")
            return
        })
        val out = File(System.getenv("BGM_SEQUEL_OUT") ?: "$workDir/bgm-sequel-seasons.tsv")
        val started = System.currentTimeMillis()
        val nodes = File(workDir, "anime.jsonl").useLines { lines ->
            lines.filter { it.isNotBlank() }
                .map { json.decodeFromString(Anime.serializer(), it) }
                .associate { it.id to it.toSeriesNode() }
        }
        val relations = File(workDir, "relations.jsonl").useLines { lines ->
            lines.filter { it.isNotBlank() }.map { json.decodeFromString(Rel.serializer(), it) }.associate { it.id to it.rel }
        }

        fun edgesOf(id: Int): SeriesEdges {
            // [关联条目 id, 关系类型, order], 已按 (order, id) 排好
            val page = relations[id].orEmpty().filter { it[0] in nodes }.take(SERIES_RELATIONS_PAGE_SIZE)
            return SeriesEdges(
                prequels = page.filter { it[1] == SERIES_RELATION_PREQUEL }.map { nodes.getValue(it[0]) },
                sequels = page.filter { it[1] == SERIES_RELATION_SEQUEL }.map { nodes.getValue(it[0]) },
            )
        }

        val rows = runBlocking {
            nodes.keys.sorted().mapNotNull { id ->
                val chain = walkPrequelChain(id, MAX_PREQUEL_HOPS, ::isSeasonFormat) { edgesOf(it) }
                sequelSeasonCandidates(chain).takeIf { it.isNotEmpty() }?.let { candidates ->
                    "$id\t${candidates.joinToString(",") { it.id.toString() }}"
                }
            }
        }
        val dump = File(workDir, "dump.name").takeIf { it.isFile }?.readText()?.trim().orEmpty().ifEmpty { "unknown" }
        val header = "# bangumi-sequel-seasons v1 rules=$SEQUEL_SEASON_RULES max_id=${nodes.keys.max()} dump=$dump" +
                " | 列: bgm_id, 可以换成的那几季 (逗号分隔的 bgm id, 按挑选顺序). 只收有候选的; 表覆盖到 (id 不超过 max_id) 却没有这一行 = 不用换. 说明见 README."
        out.parentFile?.mkdirs()
        out.writeText((listOf(header) + rows).joinToString("\n", postfix = "\n"))
        println("续作候选季: ${nodes.size} 部动画里 ${rows.size} 部有候选, ${System.currentTimeMillis() - started}ms -> $out")
    }

    /** 与 app 取 `/p1/subjects/{id}/relations` 时构造的节点逐字段对应: 首播日、集数取自同一份数据. */
    private fun Anime.toSeriesNode() = SeriesNode(
        id = id,
        name = name,
        nameCn = nameCN,
        imageLarge = "",
        metaTags = meta,
        nsfw = nsfw,
        airDate = PackedDate.parseFromDate(date),
        episodes = eps.takeIf { it > 0 },
    )

    private companion object {
        val json = Json { ignoreUnknownKeys = true }
    }
}
