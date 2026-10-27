package ai.jarves.companion

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Test

private class FakeMic(var heard: Int = VOICE_RATE, var opens: Boolean = true) : Mic {
    var open = false
    var starts = 0
    override fun start(): Boolean {
        if (!opens) return false
        open = true; starts++
        return true
    }

    override fun stop(): ShortArray {
        open = false
        return ShortArray(heard)
    }
}

class TalkMachineTest {
    @Test
    fun holdAndReleaseSendsOnceAndTheMicIsClosedAtOnce() {
        val mic = FakeMic(); val m = TalkMachine(mic)
        m.press(true)
        assertEquals(Talk.Listening, m.state); assert(mic.open)
        assertNotNull(m.release())
        assertEquals(Talk.Sending, m.state); assert(!mic.open)
        m.finished(Voice.Busy)
        assertEquals(Talk.Done(Voice.Busy), m.state)
    }

    @Test
    fun theCardLeavingTheScreenWhileHeldClosesTheMicSendsNothingAndLaterPressesWork() {
        val mic = FakeMic(); val m = TalkMachine(mic)
        m.press(true)
        m.cancel()                                                      // pointer coroutine cancelled / link down / drag off
        assert(!mic.open); assertEquals(Talk.Idle, m.state)
        assertNull(m.release())                                         // a late release finds nothing to send
        m.press(true)
        assertEquals(Talk.Listening, m.state); assertEquals(2, mic.starts)
    }

    @Test
    fun unpairClosesTheMicAndClearsAnOldAnswer() {
        val mic = FakeMic(); val m = TalkMachine(mic)
        m.press(true); m.reset()
        assert(!mic.open); assertEquals(Talk.Idle, m.state)
        m.press(true); m.release(); m.finished(Voice.NoSpeech)
        assertEquals(Talk.Done(Voice.NoSpeech), m.state)
        m.reset()
        assertEquals(Talk.Idle, m.state)
    }

    @Test
    fun aSecondPressWhileListeningOrSendingIsIgnoredAndATapIsNotSent() {
        val mic = FakeMic(); val m = TalkMachine(mic)
        m.press(true); m.press(true)
        assertEquals(1, mic.starts)
        mic.heard = 10
        assertNull(m.release()); assertEquals(Talk.Idle, m.state)
        mic.heard = VOICE_RATE
        m.press(true); m.release(); m.press(true)
        assertEquals(Talk.Sending, m.state)
    }

    @Test
    fun noMicNotReadyAndDeniedAllShowTheMicProblemAndNeverOpenIt() {
        val bad = FakeMic(opens = false)
        TalkMachine(bad).also { it.press(true); assertEquals(Talk.NoMic, it.state) }
        val mic = FakeMic()
        TalkMachine(mic).also { it.press(false); assertEquals(Talk.NoMic, it.state); assert(!mic.open) }
        TalkMachine(mic).also { it.denied(); assertEquals(Talk.NoMic, it.state) }
    }
}
