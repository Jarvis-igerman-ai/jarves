package ai.jarves.companion

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.LifecycleStartEffect
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

// ---------- Pure logic (JVM unit-tested, no Android classes) ----------

/** Why the phone shows only Deny for an approval. */
enum class DenyReason { NONE, PHONE_OFF, PC_ONLY }

/** Which buttons one approval gets. Approve exists ONLY when the PC says the phone may approve it; Deny is always there. */
data class ApprovalButtons(val approve: Boolean, val reason: DenyReason)

fun approvalButtons(a: Approval, phoneApprovalsOn: Boolean): ApprovalButtons = when {
    !phoneApprovalsOn -> ApprovalButtons(false, DenyReason.PHONE_OFF)
    !a.phoneCan -> ApprovalButtons(false, DenyReason.PC_ONLY)
    else -> ApprovalButtons(true, DenyReason.NONE)
}

enum class Risk { LOW, MEDIUM, HIGH }

/** Unknown risk words are shown as high, like the PC popup does. */
fun riskOf(s: String): Risk = when (s.trim().lowercase()) {
    "low" -> Risk.LOW
    "medium", "med" -> Risk.MEDIUM
    else -> Risk.HIGH
}

fun riskText(r: Risk): Int = when (r) {
    Risk.LOW -> R.string.appr_risk_low
    Risk.MEDIUM -> R.string.appr_risk_medium
    Risk.HIGH -> R.string.appr_risk_high
}

fun reasonText(r: DenyReason): Int? = when (r) {
    DenyReason.NONE -> null
    DenyReason.PHONE_OFF -> R.string.appr_reason_off
    DenyReason.PC_ONLY -> R.string.appr_reason_pc_only
}

/** Every answer of the PC to a decision, as one plain sentence. */
fun decisionText(d: Decision, approve: Boolean): Int = when (d) {
    Decision.DONE -> if (approve) R.string.appr_done_approved else R.string.appr_done_denied
    Decision.OFF -> R.string.appr_dec_off
    Decision.PC_ONLY -> R.string.appr_dec_pc_only
    Decision.GONE -> R.string.appr_dec_gone
    Decision.RATE -> R.string.appr_dec_rate
    Decision.NOT_ALLOWED -> R.string.appr_dec_not_allowed
    Decision.REVOKED -> R.string.appr_dec_revoked
    Decision.UNREACHABLE -> R.string.appr_dec_unreachable
}

/** Only an answer that may not have reached the PC (or was not processed) is offered again, with the SAME command id. */
fun canRetry(d: Decision): Boolean = d == Decision.UNREACHABLE || d == Decision.RATE

/** One owner tap: the approval, approve or deny, and the one command id made for this tap. */
data class Tap(val id: String, val approve: Boolean, val commandId: String)

data class Outcome(val tap: Tap, val decision: Decision)

data class ApprovalsState(
    val token: String? = null,
    val list: Approvals? = null,
    val loading: Boolean = false,
    val sending: Tap? = null,
    val last: Outcome? = null,
)

/**
 * A new tap. Null (nothing is sent) while another decision is on its way, when the approval is not in the list,
 * or when Approve was asked for an approval the phone may not approve.
 */
fun startTap(s: ApprovalsState, id: String, approve: Boolean, newId: () -> String = ::newCommandId): Tap? {
    if (s.sending != null) return null
    val listed = s.list as? Approvals.Listed ?: return null
    val a = listed.pending.firstOrNull { it.id == id } ?: return null
    if (approve && !approvalButtons(a, listed.phoneApprovalsOn).approve) return null
    return Tap(id, approve, newId())
}

/** "Try again" resends the previous tap unchanged (same command id), so the PC can never decide twice. */
fun retryTap(s: ApprovalsState): Tap? {
    if (s.sending != null) return null
    val o = s.last ?: return null
    return if (canRetry(o.decision)) o.tap else null
}

fun afterDecision(s: ApprovalsState, tap: Tap, d: Decision): ApprovalsState = s.copy(sending = null, last = Outcome(tap, d))

// ---------- State holder (lives in AppModel, so a decision in flight survives a rotation) ----------

class ApprovalsModel(
    private val scope: CoroutineScope,
    private val io: CoroutineDispatcher = Dispatchers.IO,
    private val clientFor: (Session) -> CompanionClient = { CompanionClient(it.base, PinnedTransport(it.pin)) },
) {
    private val _state = MutableStateFlow(ApprovalsState())
    val state: StateFlow<ApprovalsState> = _state
    private var load: Job? = null

    private fun forSession(s: Session) {
        if (_state.value.token != s.token) {
            load?.cancel()
            _state.value = ApprovalsState(token = s.token)
        }
    }

    /** Reads the list once. Called on screen start, by the Refresh button, on a visible-screen status tick and after each decision. */
    fun refresh(s: Session, force: Boolean = false) {
        forSession(s)
        if (load?.isActive == true) {
            if (!force) return
            load?.cancel()
        }
        _state.update { it.copy(loading = true) }
        load = scope.launch {
            val r = try {
                withContext(io) { clientFor(s).approvals(s.token) }
            } catch (e: Exception) {
                if (e is kotlinx.coroutines.CancellationException) throw e
                Approvals.Unreachable
            }
            _state.update { if (it.token == s.token) it.copy(list = r, loading = false) else it }
        }
    }

    fun tap(s: Session, id: String, approve: Boolean) {
        forSession(s)
        startTap(_state.value, id, approve)?.let { send(s, it) }
    }

    fun retry(s: Session) {
        forSession(s)
        retryTap(_state.value)?.let { send(s, it) }
    }

    private fun send(s: Session, tap: Tap) {
        _state.update { it.copy(sending = tap) }
        scope.launch {
            val d = try {
                withContext(io) { clientFor(s).decide(s.token, tap.id, tap.approve, tap.commandId) }
            } catch (e: Exception) {
                if (e is kotlinx.coroutines.CancellationException) throw e
                Decision.UNREACHABLE
            }
            _state.update { if (it.token == s.token) afterDecision(it, tap, d) else it }
            refresh(s, force = true)
        }
    }
}

// ---------- Compose ----------

private val Tall = Modifier.heightIn(min = 48.dp)

/** Waiting approvals, shown under the status while the link is Online. Read only from the paired PC; nothing is deleted here. */
@Composable
fun ApprovalsCard(session: Session, link: Link, model: ApprovalsModel) {
    // Refresh when the screen starts and on each visible-screen status tick (AppModel polls every 10 s only while visible).
    LifecycleStartEffect(session.token) {
        model.refresh(session)
        onStopOrDispose { }
    }
    LaunchedEffect(link) { model.refresh(session) }
    val st by model.state.collectAsStateWithLifecycle()
    val s = if (st.token == session.token) st else ApprovalsState(token = session.token)

    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(
                    stringResource(R.string.appr_title), style = MaterialTheme.typography.titleMedium,
                    modifier = Modifier.weight(1f).semantics { heading() },
                )
                val refreshCd = stringResource(R.string.appr_refresh_cd)
                OutlinedButton(
                    { model.refresh(session, force = true) }, enabled = !s.loading,
                    modifier = Tall.semantics { contentDescription = refreshCd },
                ) { Text(stringResource(R.string.appr_refresh)) }
            }
            s.last?.let { o ->
                Text(stringResource(decisionText(o.decision, o.tap.approve)), color = outcomeColor(o.decision))
                if (retryTap(s) != null) {
                    val retryCd = stringResource(R.string.appr_retry_cd)
                    OutlinedButton({ model.retry(session) }, modifier = Tall.fillMaxWidth().semantics { contentDescription = retryCd }) {
                        Text(stringResource(R.string.appr_retry))
                    }
                }
            }
            when (val l = s.list) {
                null -> Text(stringResource(R.string.appr_loading))
                Approvals.NotAllowed -> Text(stringResource(R.string.appr_list_not_allowed))
                Approvals.Revoked -> Text(stringResource(R.string.appr_list_revoked), color = MaterialTheme.colorScheme.error)
                Approvals.Unreachable -> Text(stringResource(R.string.appr_list_unreachable), color = MaterialTheme.colorScheme.error)
                is Approvals.Listed -> {
                    if (l.pending.isEmpty()) {
                        Text(stringResource(R.string.appr_none))
                    } else {
                        l.pending.forEachIndexed { i, a ->
                            if (i > 0) HorizontalDivider()
                            ApprovalRow(a, approvalButtons(a, l.phoneApprovalsOn), busy = s.sending != null) { approve ->
                                model.tap(session, a.id, approve)
                            }
                        }
                    }
                }
            }
            if (s.sending != null) Text(stringResource(R.string.appr_sending))
        }
    }
}

@Composable
private fun outcomeColor(d: Decision) =
    if (d == Decision.DONE) MaterialTheme.colorScheme.onSurface else MaterialTheme.colorScheme.error

@Composable
private fun ApprovalRow(a: Approval, b: ApprovalButtons, busy: Boolean, onDecide: (Boolean) -> Unit) {
    // A local countdown between refreshes; nothing is asked from the PC for it.
    var left by remember(a.id, a.secondsLeft) { mutableIntStateOf(a.secondsLeft.coerceAtLeast(0)) }
    LaunchedEffect(a.id, a.secondsLeft) {
        while (left > 0) {
            delay(1_000)
            left -= 1
        }
    }
    val risk = riskOf(a.risk)
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Text(a.title.ifBlank { "…" }, style = MaterialTheme.typography.bodyLarge, fontWeight = FontWeight.Medium)
        Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            // Risk is written out; color only repeats it.
            Text(
                stringResource(riskText(risk)), style = MaterialTheme.typography.bodyMedium,
                color = if (risk == Risk.HIGH) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurfaceVariant,
                fontWeight = if (risk == Risk.HIGH) FontWeight.Bold else FontWeight.Normal,
            )
            Text(
                if (left > 0) stringResource(R.string.appr_seconds_left, left) else stringResource(R.string.appr_time_up),
                style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        val approveCd = stringResource(R.string.appr_approve_cd, a.title)
        val denyCd = stringResource(R.string.appr_deny_cd, a.title)
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.fillMaxWidth()) {
            if (b.approve) {
                Button({ onDecide(true) }, enabled = !busy, modifier = Tall.weight(1f).semantics { contentDescription = approveCd }) {
                    Text(stringResource(R.string.appr_approve))
                }
            }
            OutlinedButton({ onDecide(false) }, enabled = !busy, modifier = Tall.weight(1f).semantics { contentDescription = denyCd }) {
                Text(stringResource(R.string.appr_deny))
            }
        }
        reasonText(b.reason)?.let { Text(stringResource(it), style = MaterialTheme.typography.bodySmall) }
    }
}
