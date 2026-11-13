// Package engine is the boundary between the bridge's policy/API layer and the WhatsApp transport. The real
// implementation (internal/engine/wm) wraps whatsmeow; Fake is an in-memory engine for the API tests. Engines only
// move data: every rule (mode, allow-lists, read gate, retention, kill) is enforced above them, in internal/api.
package engine

import (
	"context"
	"errors"

	"github.com/Jarvis-igerman-ai/jarves/bridge/whatsapp/internal/msgstore"
)

// Connection states reported by GET /status.
const (
	StateNotLinked    = "not_linked"
	StateLinking      = "linking"
	StateConnecting   = "connecting"
	StateConnected    = "connected"
	StateDisconnected = "disconnected"
	StateLoggedOut    = "logged_out"
)

// Link states reported by GET /link.
const (
	LinkIdle    = "idle"
	LinkWaiting = "waiting"
	LinkLinked  = "linked"
	LinkTimeout = "timeout"
	LinkError   = "error"
)

// Account warning codes (account.warning {code}); each one makes the bridge kill itself.
const (
	WarnTemporaryBan      = "temporary_ban"
	WarnStreamReplaced    = "stream_replaced"
	WarnClientOutdated    = "client_outdated"
	WarnAccountRestricted = "account_restricted"
)

// ErrNotConnected is returned by SendText when the engine is offline.
var ErrNotConnected = errors.New("not connected")

// ErrAlreadyLinked is returned by StartLink when a device is already linked.
var ErrAlreadyLinked = errors.New("already linked")

// Event is anything an engine reports.
type Event interface{ isEvent() }

// MessageEvent = one message (live, or from a history sync when History is true; OnDemand = the answer to a
// RequestHistory call).
type MessageEvent struct {
	Msg      msgstore.Message
	Chat     msgstore.ChatInfo
	History  bool
	OnDemand bool
	// RequestID = the id of the on-demand request this message answers, as WhatsApp echoes it ("" if unknown).
	RequestID string
}

// HistoryAnswerEvent = one on-demand history answer was fully delivered (its MessageEvents came first). Chats lists
// the conversations it carried; empty means WhatsApp had nothing more to send.
// RequestID is the id of the request it answers, as WhatsApp echoes it ("" if the transport did not say).
type HistoryAnswerEvent struct {
	Chats     []string
	RequestID string
}

// EditEvent = the sender edited a message.
type EditEvent struct{ Chat, ID, Text string }

// RevokeEvent = the sender deleted a message for everyone.
type RevokeEvent struct{ Chat, ID string }

// ReceiptEvent = delivery/read state of one of the owner's messages (state: delivered | read | played).
type ReceiptEvent struct{ Chat, ID, State string }

// ConnectionEvent = the connection state changed.
type ConnectionEvent struct{ State string }

// AccountWarningEvent = WhatsApp signalled account risk (temporary ban, stream replaced, outdated, restricted).
type AccountWarningEvent struct{ Code string }

// LoggedOutEvent = the device was unlinked (from the phone or by WhatsApp).
type LoggedOutEvent struct{ Reason string }

// ContactEvent = a contact or chat name became known (only updates chats that already exist).
type ContactEvent struct{ Chat msgstore.ChatInfo }

// LinkEvent = linking progress (QR code rotation, pairing code, success, timeout, error).
type LinkEvent struct {
	State       string
	QR          string
	PairingCode string
	ExpiresAt   float64
}

func (MessageEvent) isEvent()        {}
func (EditEvent) isEvent()           {}
func (RevokeEvent) isEvent()         {}
func (ReceiptEvent) isEvent()        {}
func (ConnectionEvent) isEvent()     {}
func (AccountWarningEvent) isEvent() {}
func (LoggedOutEvent) isEvent()      {}
func (ContactEvent) isEvent()        {}
func (LinkEvent) isEvent()           {}
func (HistoryAnswerEvent) isEvent()  {}

// Engine is a WhatsApp transport.
type Engine interface {
	// Name is "whatsmeow" or "fake" (endpoint.json, /status).
	Name() string
	// SetHandler installs the single event callback (called from engine goroutines).
	SetHandler(func(Event))
	IsLoggedIn() bool
	IsConnected() bool
	// State is one of the State* constants.
	State() string
	// Connect starts (or resumes) the connection of a linked device.
	Connect(ctx context.Context) error
	// Disconnect closes the connection (returns quickly; used by the kill switch).
	Disconnect()
	// Logout unlinks the device on WhatsApp's side (when possible) and deletes the device keys.
	Logout(ctx context.Context) error
	// StartLink begins linking: QR codes (phone == "") or a pairing code for phone ("+972..."). Progress = LinkEvent.
	StartLink(ctx context.Context, phone string) error
	// SendText sends one text message to one person. Returns the message id and server timestamp.
	SendText(ctx context.Context, to, text string) (id string, ts float64, err error)
	// RequestHistory asks the phone for up to count messages of chat sent before anchor (on-demand history
	// sync). Results arrive later as MessageEvent{History: true}; the API layer applies every rule to them.
	// id (from NewRequestID) is sent as the request's message id, so its answer can be matched to it.
	RequestHistory(ctx context.Context, id, chat string, anchor HistoryAnchor, count int) error
	// NewRequestID returns a fresh message id for RequestHistory.
	NewRequestID() string
}

// HistoryAnchor is the oldest message already known for a chat; on-demand history returns messages before it.
type HistoryAnchor struct {
	ID     string
	FromMe bool
	TS     float64
}
