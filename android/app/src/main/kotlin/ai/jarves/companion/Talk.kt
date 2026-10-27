package ai.jarves.companion

/** The push-to-talk card: idle, listening (button held), sending, or the last answer. Nothing here is stored. */
sealed interface Talk {
    data object Idle : Talk
    data object Listening : Talk
    data object Sending : Talk
    data object NoMic : Talk
    data class Done(val result: Voice) : Talk
}

/** The microphone as the state machine sees it (the real one is [Recorder]; tests use a fake). */
interface Mic {
    fun start(): Boolean
    fun stop(): ShortArray
}

/**
 * The whole rule set of push-to-talk, free of Android classes so it is unit-tested: the microphone is open ONLY in [Talk.Listening];
 * every other way out of it (cancel, leaving the screen, unpair, the link going down, denied permission) closes it and sends NOTHING.
 */
class TalkMachine(private val mic: Mic) {
    var state: Talk = Talk.Idle
        private set

    /** The button went down. [ready] = paired, screen visible, PC online. Refused while already listening or sending. */
    fun press(ready: Boolean) {
        if (state is Talk.Sending || state is Talk.Listening) return
        state = if (ready && mic.start()) Talk.Listening else Talk.NoMic
    }

    /** The finger was lifted on the button (or the 55 s cap was reached): the recording to send, or null when nothing should be sent. */
    fun release(): ShortArray? {
        if (state !is Talk.Listening) return null
        val heard = mic.stop()
        if (heard.size < VOICE_MIN_SAMPLES) {
            state = Talk.Idle
            return null
        }
        state = Talk.Sending
        return heard
    }

    /** Anything else that ends a hold: the microphone closes, the audio is thrown away. Also clears an old answer. */
    fun cancel() {
        if (state is Talk.Listening) mic.stop()
        if (state !is Talk.Sending) state = Talk.Idle
    }

    fun denied() {
        if (state !is Talk.Listening && state !is Talk.Sending) state = Talk.NoMic
    }

    fun finished(r: Voice) {
        if (state is Talk.Sending) state = Talk.Done(r)
    }

    /** Unpairing forgets everything, including an answer still on screen and a send in flight. */
    fun reset() {
        if (state is Talk.Listening) mic.stop()
        state = Talk.Idle
    }
}
