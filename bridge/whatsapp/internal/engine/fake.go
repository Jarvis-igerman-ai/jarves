package engine

import (
	"context"
	"errors"
	"fmt"
	"sync"
	"time"
)

// FakeSent is one text the fake engine "sent".
type FakeSent struct{ To, Text string }

// Fake is an in-memory engine for tests: no network, scripted by the test through Emit / CompleteLink.
type Fake struct {
	mu          sync.Mutex
	handler     func(Event)
	linked      bool
	connected   bool
	linking     bool
	loggedOut   bool
	Sent        []FakeSent
	SendErr     error
	Disconnects int
	Connects    int
	Logouts     int
	seq         int
	Now         func() time.Time
}

// NewFake returns a fake engine; linked=true simulates an already linked device.
func NewFake(linked bool) *Fake { return &Fake{linked: linked, Now: time.Now} }

func (f *Fake) Name() string { return "fake" }

func (f *Fake) SetHandler(h func(Event)) { f.mu.Lock(); f.handler = h; f.mu.Unlock() }

// Emit delivers an event to the bridge synchronously (as the real engine does from its goroutines).
func (f *Fake) Emit(e Event) {
	f.mu.Lock()
	h := f.handler
	f.mu.Unlock()
	if h != nil {
		h(e)
	}
}

func (f *Fake) IsLoggedIn() bool  { f.mu.Lock(); defer f.mu.Unlock(); return f.linked }
func (f *Fake) IsConnected() bool { f.mu.Lock(); defer f.mu.Unlock(); return f.connected }

func (f *Fake) State() string {
	f.mu.Lock()
	defer f.mu.Unlock()
	switch {
	case f.linking:
		return StateLinking
	case f.loggedOut:
		return StateLoggedOut
	case !f.linked:
		return StateNotLinked
	case f.connected:
		return StateConnected
	default:
		return StateDisconnected
	}
}

func (f *Fake) Connect(ctx context.Context) error {
	f.mu.Lock()
	if !f.linked {
		f.mu.Unlock()
		return errors.New("not linked")
	}
	was := f.connected
	f.connected = true
	f.Connects++
	f.mu.Unlock()
	if !was {
		f.Emit(ConnectionEvent{State: StateConnected})
	}
	return nil
}

func (f *Fake) Disconnect() {
	f.mu.Lock()
	was := f.connected
	f.connected = false
	f.Disconnects++
	f.mu.Unlock()
	if was {
		f.Emit(ConnectionEvent{State: StateDisconnected})
	}
}

func (f *Fake) Logout(ctx context.Context) error {
	f.mu.Lock()
	f.linked, f.connected, f.loggedOut, f.linking = false, false, false, false
	f.Logouts++
	f.mu.Unlock()
	return nil
}

func (f *Fake) StartLink(ctx context.Context, phone string) error {
	f.mu.Lock()
	if f.linked {
		f.mu.Unlock()
		return ErrAlreadyLinked
	}
	f.linking = true
	f.mu.Unlock()
	exp := float64(f.Now().Add(60*time.Second).UnixNano()) / 1e9
	if phone != "" {
		f.Emit(LinkEvent{State: LinkWaiting, PairingCode: "FAKE-CODE", ExpiresAt: exp})
	} else {
		f.Emit(LinkEvent{State: LinkWaiting, QR: "2@fake-qr-code,not-a-real-session", ExpiresAt: exp})
	}
	return nil
}

// CompleteLink simulates the owner scanning the QR code.
func (f *Fake) CompleteLink() {
	f.mu.Lock()
	f.linking, f.linked, f.loggedOut = false, true, false
	f.mu.Unlock()
	f.Emit(LinkEvent{State: LinkLinked})
}

// SimulateLoggedOut simulates an unlink from the phone.
func (f *Fake) SimulateLoggedOut(reason string) {
	f.mu.Lock()
	f.linked, f.connected, f.loggedOut = false, false, true
	f.mu.Unlock()
	f.Emit(LoggedOutEvent{Reason: reason})
}

func (f *Fake) SendText(ctx context.Context, to, text string) (string, float64, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if !f.connected {
		return "", 0, ErrNotConnected
	}
	if f.SendErr != nil {
		return "", 0, f.SendErr
	}
	f.seq++
	f.Sent = append(f.Sent, FakeSent{To: to, Text: text})
	return fmt.Sprintf("FAKE%06d", f.seq), float64(f.Now().UnixNano()) / 1e9, nil
}
