package ai.jarves.companion

import android.content.Intent
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

// ---------- Pure logic (JVM unit-tested) ----------

const val ACTION_SEND = "android.intent.action.SEND"

/** Text from a Share sheet: only ACTION_SEND with text/plain and EXTRA_TEXT. Streams and files are never read. Null = nothing to show. */
fun sharedTextOf(action: String?, type: String?, text: CharSequence?): String? {
    if (action != ACTION_SEND) return null
    if (type?.substringBefore(';')?.trim()?.lowercase() != "text/plain") return null
    return cleanShared(text).ifEmpty { null }
}

sealed interface ShareState {
    data object None : ShareState

    /** The owner reads and may edit the text. Nothing has been sent. */
    data class Editing(val text: String) : ShareState

    data class Sending(val text: String, val commandId: String) : ShareState

    data class Result(val text: String, val commandId: String, val result: Shared) : ShareState
}

fun shareFailureText(r: Shared): Int? = when (r) {
    is Shared.Answer -> null
    Shared.Busy -> R.string.share_busy
    Shared.RateLimited -> R.string.share_rate
    Shared.NotAllowed -> R.string.share_not_allowed
    Shared.Revoked -> R.string.share_revoked
    Shared.Unreachable -> R.string.share_unreachable
}

/** A failure the PC may not have processed: "Try again" resends the same text with the SAME command id. */
fun shareCanRetry(r: Shared): Boolean = r == Shared.Unreachable || r == Shared.RateLimited || r == Shared.Busy

/** A share that arrives when the phone is not paired is dropped, never kept for later. A share while sending is ignored. */
fun offerShare(cur: ShareState, text: String?, paired: Boolean): ShareState = when {
    !paired -> ShareState.None
    text == null -> cur
    cur is ShareState.Sending -> cur
    else -> ShareState.Editing(text)
}

/** Send was pressed: a new command id for this tap. Null = nothing to send (empty text, or already sending). */
fun startShare(cur: ShareState, newId: () -> String = ::newCommandId): ShareState.Sending? {
    val t = (cur as? ShareState.Editing)?.text ?: return null
    if (cleanShared(t).isEmpty()) return null
    return ShareState.Sending(t, newId())
}

fun retryShare(cur: ShareState): ShareState.Sending? {
    val r = cur as? ShareState.Result ?: return null
    return if (shareCanRetry(r.result)) ShareState.Sending(r.text, r.commandId) else null
}

// ---------- State holder (lives in AppModel) ----------

class ShareModel(
    private val scope: CoroutineScope,
    private val io: CoroutineDispatcher = Dispatchers.IO,
    private val clientFor: (Session) -> CompanionClient = { CompanionClient(it.base, PinnedTransport(it.pin)) },
) {
    private val _state = MutableStateFlow<ShareState>(ShareState.None)
    val state: StateFlow<ShareState> = _state

    /** From onCreate/onNewIntent. Reads EXTRA_TEXT only; a broken extra from another app is ignored, never a crash. */
    fun take(intent: Intent?, paired: Boolean) {
        if (intent == null || intent.flags and Intent.FLAG_ACTIVITY_LAUNCHED_FROM_HISTORY != 0) return
        val text = try {
            sharedTextOf(intent.action, intent.type, intent.getCharSequenceExtra(Intent.EXTRA_TEXT))
        } catch (e: RuntimeException) {
            null
        }
        offer(text, paired)
    }

    fun offer(text: String?, paired: Boolean) = _state.update { offerShare(it, text, paired) }

    fun edit(text: String) {
        _state.update { if (it is ShareState.Editing || it is ShareState.Result) ShareState.Editing(text.take(SHARE_MAX_CHARS)) else it }
    }

    /** Only the Send button calls this. */
    fun send(s: Session) = startShare(_state.value)?.let { go(s, it) }

    fun retry(s: Session) = retryShare(_state.value)?.let { go(s, it) }

    fun clear() {
        _state.value = ShareState.None
    }

    private fun go(s: Session, sending: ShareState.Sending) {
        _state.value = sending
        scope.launch {
            val r = try {
                withContext(io) { clientFor(s).share(s.token, sending.text, sending.commandId) }
            } catch (e: Exception) {
                if (e is kotlinx.coroutines.CancellationException) throw e
                Shared.Unreachable
            }
            _state.update { if (it == sending) ShareState.Result(sending.text, sending.commandId, r) else it }
        }
    }
}

// ---------- Compose ----------

private val Tall = Modifier.heightIn(min = 48.dp)

/** The paired screen: a pending share replaces the status until it is sent or cancelled; otherwise status plus approvals. */
@Composable
fun PairedScreen(x: Screen.Status, share: ShareModel, approvals: ApprovalsModel, onUnpair: () -> Unit) {
    val sh by share.state.collectAsStateWithLifecycle()
    if (sh != ShareState.None) {
        ShareScreen(x.session, sh, share)
    } else {
        StatusScreen(x, onUnpair)
        val link = x.link
        if (link is Link.Online) ApprovalsCard(x.session, link, approvals)
    }
}

@Composable
fun ShareScreen(session: Session, st: ShareState, model: ShareModel) {
    Text(stringResource(R.string.share_title), style = MaterialTheme.typography.headlineSmall)
    Text(stringResource(R.string.share_help))
    val text = when (st) {
        is ShareState.Editing -> st.text
        is ShareState.Sending -> st.text
        is ShareState.Result -> st.text
        ShareState.None -> ""
    }
    val sending = st is ShareState.Sending
    val answered = (st as? ShareState.Result)?.result as? Shared.Answer
    OutlinedTextField(
        text, model::edit, enabled = !sending && answered == null,
        label = { Text(stringResource(R.string.share_field)) },
        supportingText = { Text(stringResource(R.string.share_count, text.length, SHARE_MAX_CHARS)) },
        minLines = 3, maxLines = 10, modifier = Modifier.fillMaxWidth(),
    )
    if (sending) Text(stringResource(R.string.share_sending))
    if (st is ShareState.Result) {
        if (answered != null) {
            Card(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    Text(stringResource(R.string.share_reply_title), style = MaterialTheme.typography.titleSmall)
                    Text(answered.reply.ifBlank { stringResource(R.string.share_sent_no_reply) })
                }
            }
        } else {
            shareFailureText(st.result)?.let { Banner(stringResource(it), true) }
        }
    }
    val sendCd = stringResource(R.string.share_send_cd)
    val retryCd = stringResource(R.string.share_retry_cd)
    val cancelCd = stringResource(R.string.share_cancel_cd)
    val doneCd = stringResource(R.string.share_done_cd)
    if (answered != null) {
        Button(model::clear, modifier = Tall.fillMaxWidth().semantics { contentDescription = doneCd }) {
            Text(stringResource(R.string.share_done))
        }
        return
    }
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.fillMaxWidth()) {
        if (st is ShareState.Result && shareCanRetry(st.result)) {
            Button({ model.retry(session) }, modifier = Tall.weight(1f).semantics { contentDescription = retryCd }) {
                Text(stringResource(R.string.share_retry))
            }
        } else {
            Button(
                { model.send(session) }, enabled = st is ShareState.Editing && cleanShared(text).isNotEmpty(),
                modifier = Tall.weight(1f).semantics { contentDescription = sendCd },
            ) { Text(stringResource(R.string.share_send)) }
        }
        OutlinedButton(model::clear, enabled = !sending, modifier = Tall.weight(1f).semantics { contentDescription = cancelCd }) {
            Text(stringResource(R.string.share_cancel))
        }
    }
}
