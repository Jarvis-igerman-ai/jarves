package ai.jarves.companion

import android.app.Application
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.viewModels
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

sealed interface Screen {
    data class Pair(val error: PairError? = null, val busy: Boolean = false) : Screen
    data class Confirm(val base: String, val name: String, val code: String, val fingerprint: String) : Screen
    data class Status(val session: Session, val link: Link?) : Screen
}

class AppModel(app: Application) : AndroidViewModel(app) {
    private val store = SessionStore(app)
    private var active = false
    private var session: Session? = null
    private val _screen = MutableStateFlow<Screen>(store.load()?.let { Screen.Status(it, null) } ?: Screen.Pair())
    val screen: StateFlow<Screen> = _screen
    private var poll: Job? = null

    init {
        session = (screen.value as? Screen.Status)?.session
    }

    /** The screen is visible: poll. In the background nothing is polled (battery, and the PC is not asked for nothing). */
    fun onStart() {
        active = true
        session?.let { startPolling(it) }
    }

    fun onStop() {
        active = false
        poll?.cancel()
    }

    private fun startPolling(s: Session) {
        session = s
        poll?.cancel()
        if (!active) return
        poll = viewModelScope.launch {
            val client = CompanionClient(s.base, PinnedTransport(s.pin))
            while (isActive) {
                val link = try {
                    withContext(Dispatchers.IO) { client.check(s.token) }
                } catch (e: Exception) {
                    Link.Unreachable
                }
                _screen.value = Screen.Status(s, link)
                if (link is Link.Revoked || link is Link.CertChanged) break      // nothing more to ask until the owner decides
                delay(10_000)
            }
        }
    }

    /** Step 1: connect once and read the certificate fingerprint. Nothing is sent. */
    fun check(base: String, name: String, code: String) {
        val b = parseBase(base)
        if (b == null) {
            _screen.value = Screen.Pair(PairError.BAD_ADDRESS)
            return
        }
        if (!Regex("\\d{8}").matches(code)) {
            _screen.value = Screen.Pair(PairError.BAD_CODE)
            return
        }
        _screen.value = Screen.Pair(busy = true)
        viewModelScope.launch {
            val fp = try {
                withContext(Dispatchers.IO) { probeFingerprint(b) }
            } catch (e: Exception) {
                null
            }
            _screen.value = if (fp == null) Screen.Pair(PairError.UNREACHABLE) else Screen.Confirm(b, name, code, fp)
        }
    }

    /** Step 2: the owner compared the fingerprint; pair over a connection pinned to exactly that certificate. */
    fun pair(c: Screen.Confirm) {
        _screen.value = Screen.Pair(busy = true)
        viewModelScope.launch {
            try {
                val p = withContext(Dispatchers.IO) { CompanionClient(c.base, PinnedTransport(c.fingerprint)).pair(c.code, c.name) }
                val s = Session(c.base, p.token, c.fingerprint, p.device)
                store.save(s)
                _screen.value = Screen.Status(s, null)
                startPolling(s)
            } catch (e: PairFailed) {
                _screen.value = Screen.Pair(e.kind)
            }
        }
    }

    fun cancel() {
        _screen.value = Screen.Pair()
    }

    fun unpair() {
        poll?.cancel()
        session = null
        store.clear()
        _screen.value = Screen.Pair()
    }
}

class MainActivity : ComponentActivity() {
    private val model: AppModel by viewModels()

    override fun onStart() {
        super.onStart()
        model.onStart()
    }

    override fun onStop() {
        model.onStop()
        super.onStop()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            MaterialTheme {
                Surface(Modifier.fillMaxSize()) {
                    val s by model.screen.collectAsStateWithLifecycle()
                    Column(Modifier.padding(16.dp).verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                        when (val x = s) {
                            is Screen.Pair -> PairScreen(x, model::check)
                            is Screen.Confirm -> ConfirmScreen(x, { model.pair(x) }, model::cancel)
                            is Screen.Status -> StatusScreen(x, model::unpair)
                        }
                    }
                }
            }
        }
    }
}

@Composable
fun PairScreen(s: Screen.Pair, onCheck: (String, String, String) -> Unit) {
    var addr by remember { mutableStateOf("") }
    var name by remember { mutableStateOf("Android") }
    var code by remember { mutableStateOf("") }
    Text(stringResource(R.string.pair_title), style = MaterialTheme.typography.headlineSmall)
    Text(stringResource(R.string.pair_help))
    OutlinedTextField(addr, { addr = it }, label = { Text(stringResource(R.string.addr)) }, singleLine = true, modifier = Modifier.fillMaxWidth())
    OutlinedTextField(name, { name = it.take(40) }, label = { Text(stringResource(R.string.device_name)) }, singleLine = true, modifier = Modifier.fillMaxWidth())
    OutlinedTextField(
        code, { code = it.filter(Char::isDigit).take(8) }, label = { Text(stringResource(R.string.code)) }, singleLine = true,
        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.NumberPassword), modifier = Modifier.fillMaxWidth(),
    )
    s.error?.let { Text(stringResource(errorText(it)), color = MaterialTheme.colorScheme.error) }
    Button({ onCheck(addr, name, code) }, enabled = !s.busy && addr.isNotBlank(), modifier = Modifier.fillMaxWidth()) {
        Text(stringResource(R.string.check_pc))
    }
}

fun errorText(e: PairError): Int = when (e) {
    PairError.BAD_ADDRESS -> R.string.err_address
    PairError.BAD_CODE -> R.string.err_code
    PairError.REFUSED -> R.string.err_refused
    PairError.RATE -> R.string.err_rate
    PairError.UNREACHABLE -> R.string.err_unreachable
    PairError.BAD_ANSWER -> R.string.err_answer
}

@Composable
fun ConfirmScreen(c: Screen.Confirm, onYes: () -> Unit, onNo: () -> Unit) {
    Text(stringResource(R.string.confirm_title), style = MaterialTheme.typography.headlineSmall)
    Text(stringResource(R.string.confirm_help))
    Card(Modifier.fillMaxWidth()) { Text(Pinning.pretty(c.fingerprint), Modifier.padding(12.dp), style = MaterialTheme.typography.bodyMedium) }
    Button(onYes, modifier = Modifier.fillMaxWidth()) { Text(stringResource(R.string.confirm_yes)) }
    OutlinedButton(onNo, modifier = Modifier.fillMaxWidth()) { Text(stringResource(R.string.confirm_no)) }
}

@Composable
fun StatusScreen(s: Screen.Status, onUnpair: () -> Unit) {
    Text(stringResource(R.string.status_title), style = MaterialTheme.typography.headlineSmall)
    val link = s.link
    when (link) {
        is Link.Online -> Banner(stringResource(R.string.online), false)
        is Link.PcOffline -> Banner(stringResource(R.string.pc_offline), true)
        Link.Unreachable -> Banner(stringResource(R.string.unreachable), true)
        Link.Revoked -> Banner(stringResource(R.string.revoked), true)
        Link.CertChanged -> Banner(stringResource(R.string.cert_changed), true)
        is Link.Incompatible -> Banner(stringResource(R.string.incompatible, link.apiMin), true)
        null -> Text("…")
    }
    val info = (link as? Link.Online)?.info ?: (link as? Link.PcOffline)?.info
    if (info != null) {
        Text(stringResource(R.string.version, info.coreVersion))
        val skew = skewSeconds(info, System.currentTimeMillis() / 1000.0).toInt()
        if (kotlin.math.abs(skew) > 120) Text(stringResource(R.string.skew, skew))
    }
    OutlinedButton(onUnpair, modifier = Modifier.fillMaxWidth()) {
        Text(stringResource(if (link == Link.Revoked || link == Link.CertChanged) R.string.pair_again else R.string.unpair))
    }
    Text(stringResource(R.string.unpair_note), style = MaterialTheme.typography.bodySmall)
}

/** The PC-offline banner: a red card when the computer or its brain is not there; nothing is queued by the app. */
@Composable
fun Banner(text: String, bad: Boolean) {
    Card(
        Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(containerColor = if (bad) MaterialTheme.colorScheme.errorContainer else MaterialTheme.colorScheme.secondaryContainer),
    ) { Text(text, Modifier.padding(12.dp)) }
}
