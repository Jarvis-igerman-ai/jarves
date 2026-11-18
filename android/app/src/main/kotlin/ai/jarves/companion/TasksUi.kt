package ai.jarves.companion

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Card
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONException
import org.json.JSONObject

// ---------- Pure logic (JVM unit-tested) ----------

/** One goal plan as the PC lists it (GET /api/m/plans). Step tools, reasons and errors are not shown on the phone. */
data class TaskPlan(val id: Int, val title: String, val state: String, val progress: Int, val stepsDone: Int, val steps: Int, val needsPc: Boolean)

sealed interface Plans {
    data class Listed(val plans: List<TaskPlan>, val enabled: Boolean) : Plans
    data object NotAllowed : Plans
    data object Revoked : Plans
    data object Unreachable : Plans
}

/** Null = not a plans answer. Entries without a numeric id are dropped. */
fun parsePlans(body: String): Plans.Listed? = try {
    val j = JSONObject(body)
    val arr = j.optJSONArray("plans")
    val list = (0 until (arr?.length() ?: 0)).mapNotNull { i ->
        val o = arr!!.optJSONObject(i) ?: return@mapNotNull null
        if (o.opt("id") !is Number) return@mapNotNull null
        val steps = o.optJSONArray("steps")
        val n = steps?.length() ?: 0
        val done = (0 until n).count { steps!!.optJSONObject(it)?.optString("status") == "done" }
        TaskPlan(
            o.getInt("id"), (if (o.isNull("title")) "" else o.optString("title", "")).take(300), o.optString("state", ""),
            o.optInt("progress", 0).coerceIn(0, 100), done, n, o.optBoolean("needs_pc", false),
        )
    }
    Plans.Listed(list, j.optBoolean("enabled", true))
} catch (e: JSONException) {
    null
}

fun taskStateText(state: String): Int = when (state.trim().lowercase()) {
    "draft" -> R.string.task_state_draft
    "ready" -> R.string.task_state_ready
    "running" -> R.string.task_state_running
    "paused" -> R.string.task_state_paused
    "blocked" -> R.string.task_state_blocked
    "failed" -> R.string.task_state_failed
    "done" -> R.string.task_state_done
    "cancelled" -> R.string.task_state_cancelled
    else -> R.string.task_state_other
}

/** The card shows only while the PC lists the `tasks` scope for this phone and has not refused it (403) or unpaired it (401). */
fun tasksCardVisible(scopes: List<String>, st: Plans?): Boolean =
    "tasks" in scopes && st != Plans.NotAllowed && st != Plans.Revoked

// ---------- State holder (lives in AppModel) ----------

class TasksModel(
    private val scope: CoroutineScope,
    private val io: CoroutineDispatcher = Dispatchers.IO,
    private val clientFor: (Session) -> CompanionClient = { CompanionClient(it.base, PinnedTransport(it.pin)) },
) {
    private val _state = MutableStateFlow<Plans?>(null)
    val state: StateFlow<Plans?> = _state
    private var token: String? = null
    private var load: Job? = null

    /** One visible-screen status tick: no read without the `tasks` scope (and a later grant starts fresh); after a 403/401 no more reads until the scope list or pairing changes. */
    fun tick(s: Session, scopes: List<String>) {
        if ("tasks" !in scopes) {
            load?.cancel()
            // Keep `token` (unlike refresh): with state null the card counts as visible, so the next grant reads again.
            _state.value = null
            return
        }
        if (token == s.token && !tasksCardVisible(scopes, _state.value)) return
        refresh(s)
    }

    /** Read once per visible-screen status tick; a read never changes anything on the PC. A newer read replaces an older one; a new pairing starts empty. */
    fun refresh(s: Session) {
        if (token != s.token) {
            token = s.token
            _state.value = null
        }
        load?.cancel()
        load = scope.launch {
            _state.value = try {
                withContext(io) { clientFor(s).plans(s.token) }
            } catch (e: Exception) {
                if (e is kotlinx.coroutines.CancellationException) throw e
                Plans.Unreachable
            }
        }
    }
}

// ---------- Compose ----------

/** The PC's tasks, read-only. Approving, skipping and deleting a step stay on the computer. Shown only when the PC lists the tasks scope for this phone. */
@Composable
fun TasksCard(session: Session, link: Link.Online, model: TasksModel) {
    // Each status tick is a new Link.Online (the PC's server_time changes per answer); tick() decides whether to read.
    LaunchedEffect(session.token, link) { model.tick(session, link.info.scopes) }
    val st by model.state.collectAsStateWithLifecycle()
    if (!tasksCardVisible(link.info.scopes, st)) return
    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Text(stringResource(R.string.task_title), style = MaterialTheme.typography.titleMedium, modifier = Modifier.semantics { heading() })
            when (val l = st) {
                is Plans.Listed -> if (!l.enabled) Text(stringResource(R.string.task_off)) else if (l.plans.isEmpty()) Text(stringResource(R.string.task_none)) else l.plans.forEach { p ->
                    Text(p.title.ifBlank { "…" }, style = MaterialTheme.typography.bodyLarge)
                    Text(
                        pluralStringResource(R.plurals.task_line, p.steps, stringResource(taskStateText(p.state)), p.stepsDone, p.steps, p.progress),
                        style = MaterialTheme.typography.bodyMedium,
                    )
                    if (p.needsPc) Text(stringResource(R.string.task_needs_pc), style = MaterialTheme.typography.bodySmall)
                }
                Plans.Unreachable -> Text(stringResource(R.string.task_unreachable), color = MaterialTheme.colorScheme.error)
                else -> Text(stringResource(R.string.task_loading))
            }
            Text(stringResource(R.string.task_pc_only), style = MaterialTheme.typography.bodySmall)
        }
    }
}
