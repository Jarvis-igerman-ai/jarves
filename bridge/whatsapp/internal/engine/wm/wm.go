// Package wm is the real engine: whatsmeow (go.mau.fi/whatsmeow, pinned in go.mod; source
// https://github.com/tulir/whatsmeow, version checked 2026-10-20) as a linked device named "JARVES bridge".
//
// Rules of this engine:
//   - the device store lives in the bridge's in-memory SQLite database (sealed by internal/securestore);
//   - it never sends read receipts, presence or typing indicators (no MarkRead / SendPresence / SendChatPresence
//     call exists in this package; whatsmeow sends only the protocol-level delivery acks any linked device sends);
//   - media is never downloaded: only mime, size and caption are reported;
//   - nothing spoofs another client: the library's own device registration is used, with DeviceProps.Os set to
//     "JARVES bridge" (the name shown under Linked devices). The pairing-code path needs a "Browser (OS)" display
//     name that WhatsApp's server validates; the library documents "Chrome (Linux)" style names, we pass
//     "Chrome (Windows)" exactly as the library's API requires (QR linking, the default, needs no such name);
//   - whatsmeow's own logging is disabled (it would print JIDs).
package wm

import (
	"context"
	"database/sql"
	"errors"
	"strings"
	"sync"
	"time"

	"go.mau.fi/whatsmeow"
	"go.mau.fi/whatsmeow/proto/waE2E"
	"go.mau.fi/whatsmeow/store"
	"go.mau.fi/whatsmeow/store/sqlstore"
	"go.mau.fi/whatsmeow/types"
	"go.mau.fi/whatsmeow/types/events"
	waLog "go.mau.fi/whatsmeow/util/log"
	"google.golang.org/protobuf/proto"

	"github.com/Jarvis-igerman-ai/jarves/bridge/whatsapp/internal/engine"
	"github.com/Jarvis-igerman-ai/jarves/bridge/whatsapp/internal/msgstore"
)

// Version is the pinned whatsmeow pseudo-version (keep equal to go.mod; build-info.json reports it).
const Version = "v0.0.0-20261017175727-b2b1e71d45e2"

// DeviceName is the linked-device name shown on the owner's phone.
const DeviceName = "JARVES bridge"

// Engine is the whatsmeow engine.
type Engine struct {
	container *sqlstore.Container

	mu        sync.Mutex
	cli       *whatsmeow.Client
	handler   func(engine.Event)
	state     string
	linking   bool
	loggedOut bool
	groupName map[string]string
}

func init() {
	store.DeviceProps.Os = proto.String(DeviceName)
}

// New opens (and upgrades) the whatsmeow tables in db and loads the first device, or a new unlinked one.
func New(ctx context.Context, db *sql.DB) (*Engine, error) {
	c := sqlstore.NewWithDB(db, "sqlite3", waLog.Noop)
	if err := c.Upgrade(ctx); err != nil {
		return nil, err
	}
	e := &Engine{container: c, groupName: map[string]string{}}
	if err := e.newClient(ctx); err != nil {
		return nil, err
	}
	return e, nil
}

func (e *Engine) newClient(ctx context.Context) error {
	dev, err := e.container.GetFirstDevice(ctx)
	if err != nil {
		return err
	}
	cli := whatsmeow.NewClient(dev, waLog.Noop)
	cli.EnableAutoReconnect = true
	cli.AddEventHandler(e.handle)
	e.mu.Lock()
	e.cli = cli
	if dev.ID == nil {
		e.state = engine.StateNotLinked
	} else {
		e.state = engine.StateDisconnected
	}
	e.mu.Unlock()
	return nil
}

func (e *Engine) client() *whatsmeow.Client { e.mu.Lock(); defer e.mu.Unlock(); return e.cli }

func (e *Engine) Name() string { return "whatsmeow" }

func (e *Engine) SetHandler(h func(engine.Event)) { e.mu.Lock(); e.handler = h; e.mu.Unlock() }

func (e *Engine) emit(ev engine.Event) {
	e.mu.Lock()
	h := e.handler
	e.mu.Unlock()
	if h != nil {
		h(ev)
	}
}

func (e *Engine) setState(s string) {
	e.mu.Lock()
	changed := e.state != s
	e.state = s
	e.mu.Unlock()
	if changed {
		e.emit(engine.ConnectionEvent{State: s})
	}
}

func (e *Engine) IsLoggedIn() bool {
	cli := e.client()
	return cli != nil && cli.Store.ID != nil
}

func (e *Engine) IsConnected() bool {
	cli := e.client()
	return cli != nil && cli.IsConnected() && cli.IsLoggedIn()
}

func (e *Engine) State() string {
	e.mu.Lock()
	defer e.mu.Unlock()
	if e.linking {
		return engine.StateLinking
	}
	if e.loggedOut {
		return engine.StateLoggedOut
	}
	return e.state
}

func (e *Engine) Connect(ctx context.Context) error {
	cli := e.client()
	if cli.Store.ID == nil {
		return errors.New("not linked")
	}
	if cli.IsConnected() {
		return nil
	}
	e.setState(engine.StateConnecting)
	if err := cli.Connect(); err != nil {
		e.setState(engine.StateDisconnected)
		return err
	}
	return nil
}

func (e *Engine) Disconnect() {
	cli := e.client()
	if cli == nil {
		return
	}
	cli.Disconnect()
	e.mu.Lock()
	linked := cli.Store.ID != nil
	e.linking = false
	e.mu.Unlock()
	if linked {
		e.setState(engine.StateDisconnected)
	} else {
		e.setState(engine.StateNotLinked)
	}
}

// Logout unlinks on WhatsApp's side when connected; the local device keys are deleted in every case and a fresh
// unlinked device is prepared.
func (e *Engine) Logout(ctx context.Context) error {
	cli := e.client()
	var err error
	if cli.Store.ID != nil {
		if cli.IsConnected() {
			err = cli.Logout(ctx)
		} else {
			err = errors.New("not connected: server-side logout skipped")
		}
		cli.Disconnect()
		if cli.Store.ID != nil {
			_ = cli.Store.Delete(ctx)
		}
	}
	cli.RemoveEventHandlers()
	if nerr := e.newClient(ctx); nerr != nil {
		return nerr
	}
	e.mu.Lock()
	e.loggedOut, e.linking = false, false
	e.mu.Unlock()
	return err
}

func (e *Engine) StartLink(ctx context.Context, phone string) error {
	cli := e.client()
	if cli.Store.ID != nil {
		return engine.ErrAlreadyLinked
	}
	cli.Disconnect()
	qrCtx, cancel := context.WithTimeout(ctx, 3*time.Minute)
	ch, err := cli.GetQRChannel(qrCtx)
	if err != nil {
		cancel()
		return err
	}
	e.mu.Lock()
	e.linking, e.loggedOut = true, false
	e.mu.Unlock()
	if err := cli.Connect(); err != nil {
		cancel()
		e.mu.Lock()
		e.linking = false
		e.mu.Unlock()
		return err
	}
	go func() {
		defer cancel()
		paired := false
		for item := range ch {
			switch item.Event {
			case whatsmeow.QRChannelEventCode:
				exp := float64(time.Now().Add(item.Timeout).UnixNano()) / 1e9
				if phone == "" {
					e.emit(engine.LinkEvent{State: engine.LinkWaiting, QR: item.Code, ExpiresAt: exp})
				} else if !paired {
					paired = true
					code, err := cli.PairPhone(qrCtx, strings.TrimPrefix(phone, "+"), true, whatsmeow.PairClientChrome, "Chrome (Windows)")
					if err != nil {
						e.finishLink(engine.LinkError)
						return
					}
					e.emit(engine.LinkEvent{State: engine.LinkWaiting, PairingCode: code,
						ExpiresAt: float64(time.Now().Add(160*time.Second).UnixNano()) / 1e9})
				}
			case "success":
				e.finishLink(engine.LinkLinked)
				return
			case "timeout":
				e.finishLink(engine.LinkTimeout)
				return
			default:
				e.finishLink(engine.LinkError)
				return
			}
		}
		e.finishLink(engine.LinkTimeout)
	}()
	return nil
}

func (e *Engine) finishLink(state string) {
	e.mu.Lock()
	e.linking = false
	e.mu.Unlock()
	if state != engine.LinkLinked {
		e.client().Disconnect()
		e.setState(engine.StateNotLinked)
	}
	e.emit(engine.LinkEvent{State: state})
}

func (e *Engine) SendText(ctx context.Context, to, text string) (string, float64, error) {
	cli := e.client()
	if !cli.IsConnected() || !cli.IsLoggedIn() {
		return "", 0, engine.ErrNotConnected
	}
	jid, err := types.ParseJID(to)
	if err != nil || (jid.Server != types.DefaultUserServer && jid.Server != types.HiddenUserServer) || jid.Device != 0 {
		return "", 0, errors.New("bad recipient")
	}
	resp, err := cli.SendMessage(ctx, jid, &waE2E.Message{Conversation: proto.String(text)})
	if err != nil {
		return "", 0, err
	}
	return resp.ID, float64(resp.Timestamp.UnixNano()) / 1e9, nil
}

// handle converts whatsmeow events. It runs on whatsmeow's goroutines.
func (e *Engine) handle(raw any) {
	switch ev := raw.(type) {
	case *events.Connected:
		e.mu.Lock()
		e.loggedOut = false
		e.mu.Unlock()
		e.setState(engine.StateConnected)
	case *events.Disconnected, *events.KeepAliveTimeout:
		if e.IsLoggedIn() {
			e.setState(engine.StateDisconnected)
		}
	case *events.LoggedOut:
		e.mu.Lock()
		e.loggedOut = true
		e.mu.Unlock()
		e.setState(engine.StateLoggedOut)
		reason := "logged_out"
		if ev.OnConnect {
			reason = "connect_failure_" + itoa(int(ev.Reason))
		}
		e.emit(engine.LoggedOutEvent{Reason: reason})
	case *events.TemporaryBan:
		e.emit(engine.AccountWarningEvent{Code: engine.WarnTemporaryBan})
	case *events.StreamReplaced:
		e.emit(engine.AccountWarningEvent{Code: engine.WarnStreamReplaced})
	case *events.ClientOutdated:
		e.emit(engine.AccountWarningEvent{Code: engine.WarnClientOutdated})
	case *events.NotifyAccountReachoutTimelock:
		if ev.IsActive {
			e.emit(engine.AccountWarningEvent{Code: engine.WarnAccountRestricted})
		}
	case *events.ConnectFailure:
		switch ev.Reason {
		case events.ConnectFailureTempBanned:
			e.emit(engine.AccountWarningEvent{Code: engine.WarnTemporaryBan})
		case events.ConnectFailureMainDeviceGone, events.ConnectFailureUnknownLogout:
			e.emit(engine.AccountWarningEvent{Code: engine.WarnAccountRestricted})
		}
	case *events.Message:
		e.onMessage(ev, false)
	case *events.HistorySync:
		e.onHistory(ev)
	case *events.Receipt:
		state := ""
		switch ev.Type {
		case types.ReceiptTypeDelivered:
			state = "delivered"
		case types.ReceiptTypeRead:
			state = "read"
		case types.ReceiptTypePlayed:
			state = "played"
		}
		if state != "" && !ev.IsFromMe {
			for _, id := range ev.MessageIDs {
				e.emit(engine.ReceiptEvent{Chat: ev.Chat.ToNonAD().String(), ID: id, State: state})
			}
		}
	case *events.PushName:
		e.emit(engine.ContactEvent{Chat: msgstore.ChatInfo{ID: ev.JID.ToNonAD().String(), Name: ev.NewPushName}})
	case *events.Contact:
		if n := ev.Action.GetFullName(); n != "" {
			e.emit(engine.ContactEvent{Chat: msgstore.ChatInfo{ID: ev.JID.ToNonAD().String(), Name: n}})
		}
	case *events.GroupInfo:
		if ev.Name != nil && ev.Name.Name != "" {
			e.emit(engine.ContactEvent{Chat: msgstore.ChatInfo{ID: ev.JID.String(), Name: ev.Name.Name}})
		}
	}
}

func itoa(n int) string {
	if n == 0 {
		return "0"
	}
	neg := n < 0
	if neg {
		n = -n
	}
	var b []byte
	for n > 0 {
		b = append([]byte{byte('0' + n%10)}, b...)
		n /= 10
	}
	if neg {
		b = append([]byte{'-'}, b...)
	}
	return string(b)
}

// chatInfo resolves the chat's display name, number and LID from whatsmeow's contact and LID stores.
func (e *Engine) chatInfo(ctx context.Context, chat types.JID, pushName string, fromMe bool) msgstore.ChatInfo {
	cli := e.client()
	info := msgstore.ChatInfo{ID: chat.String(), IsGroup: chat.Server == types.GroupServer}
	switch chat.Server {
	case types.GroupServer:
		e.mu.Lock()
		info.Name = e.groupName[info.ID]
		e.mu.Unlock()
	case types.DefaultUserServer, types.HiddenUserServer:
		if c, err := e.contact(ctx, chat); err == nil && c.Found {
			info.Name = firstNonEmpty(c.FullName, c.FirstName, c.PushName, c.BusinessName)
		}
		if info.Name == "" && !fromMe {
			info.Name = pushName
		}
		lids := cli.Store.LIDs
		if cli.Store.ID == nil {
			lids = nil
		}
		if chat.Server == types.DefaultUserServer {
			info.Number = "+" + chat.User
			if lids != nil {
				if lid, err := lids.GetLIDForPN(ctx, chat); err == nil && !lid.IsEmpty() {
					info.LID = lid.String()
				}
			}
		} else {
			info.LID = chat.String()
			if lids != nil {
				if pn, err := lids.GetPNForLID(ctx, chat); err == nil && !pn.IsEmpty() {
					info.Number = "+" + pn.User
				}
			}
		}
	}
	return info
}

// contact looks a user up in whatsmeow's contact store (empty while no device is linked).
func (e *Engine) contact(ctx context.Context, jid types.JID) (types.ContactInfo, error) {
	st := e.client().Store
	if st.ID == nil || st.Contacts == nil {
		return types.ContactInfo{}, errors.New("no device")
	}
	return st.Contacts.GetContact(ctx, jid)
}

func firstNonEmpty(v ...string) string {
	for _, s := range v {
		if s != "" {
			return s
		}
	}
	return ""
}

func (e *Engine) onMessage(ev *events.Message, history bool) {
	ctx := context.Background()
	chat := ev.Info.Chat.ToNonAD()
	if chat.Server != types.DefaultUserServer && chat.Server != types.HiddenUserServer && chat.Server != types.GroupServer {
		return // status broadcasts, newsletters, broadcast lists: ignored
	}
	m := ev.Message
	if pm := m.GetProtocolMessage(); pm != nil {
		target := pm.GetKey().GetID()
		switch pm.GetType() {
		case waE2E.ProtocolMessage_REVOKE:
			e.emit(engine.RevokeEvent{Chat: chat.String(), ID: target})
		case waE2E.ProtocolMessage_MESSAGE_EDIT:
			kind, text, _ := classify(pm.GetEditedMessage())
			if kind == "text" {
				e.emit(engine.EditEvent{Chat: chat.String(), ID: target, Text: text})
			}
		}
		return
	}
	kind, text, media := classify(m)
	if kind == "" {
		return // reactions, polls updates, key distribution, etc.
	}
	sender := ""
	if !ev.Info.IsFromMe {
		sender = ev.Info.PushName
		if c, err := e.contact(ctx, ev.Info.Sender.ToNonAD()); err == nil && c.Found {
			sender = firstNonEmpty(c.FullName, c.FirstName, c.PushName, sender)
		}
	}
	e.emit(engine.MessageEvent{
		Msg: msgstore.Message{ID: ev.Info.ID, Chat: chat.String(), FromMe: ev.Info.IsFromMe, SenderName: sender,
			TS: float64(ev.Info.Timestamp.UnixNano()) / 1e9, Kind: kind, Text: text, Media: media, Edited: ev.IsEdit},
		Chat:    e.chatInfo(ctx, chat, ev.Info.PushName, ev.Info.IsFromMe),
		History: history,
	})
}

// classify returns (kind, text, media metadata). kind "" = not a user-visible message.
func classify(m *waE2E.Message) (string, string, *msgstore.Media) {
	if m == nil {
		return "", "", nil
	}
	meta := func(mime string, size uint64, caption string) *msgstore.Media {
		return &msgstore.Media{Mime: mime, Size: int64(size), Caption: caption}
	}
	switch {
	case m.Conversation != nil:
		return "text", m.GetConversation(), nil
	case m.ExtendedTextMessage != nil:
		return "text", m.GetExtendedTextMessage().GetText(), nil
	case m.ImageMessage != nil:
		x := m.GetImageMessage()
		return "image", "", meta(x.GetMimetype(), x.GetFileLength(), x.GetCaption())
	case m.VideoMessage != nil:
		x := m.GetVideoMessage()
		return "video", "", meta(x.GetMimetype(), x.GetFileLength(), x.GetCaption())
	case m.AudioMessage != nil:
		x := m.GetAudioMessage()
		return "audio", "", meta(x.GetMimetype(), x.GetFileLength(), "")
	case m.DocumentMessage != nil:
		x := m.GetDocumentMessage()
		return "document", "", meta(x.GetMimetype(), x.GetFileLength(), x.GetCaption())
	case m.StickerMessage != nil:
		x := m.GetStickerMessage()
		return "sticker", "", meta(x.GetMimetype(), x.GetFileLength(), "")
	case m.LocationMessage != nil || m.LiveLocationMessage != nil:
		return "location", "", nil
	case m.ContactMessage != nil || m.ContactsArrayMessage != nil:
		return "contact", "", nil
	case m.ReactionMessage != nil, m.PollUpdateMessage != nil, m.KeepInChatMessage != nil, m.PinInChatMessage != nil,
		onlySKD(m):
		return "", "", nil
	}
	return "other", "", nil
}

// onlySKD reports whether m carries nothing but a sender key distribution (group key plumbing, not a message).
func onlySKD(m *waE2E.Message) bool {
	c := proto.Clone(m).(*waE2E.Message)
	c.SenderKeyDistributionMessage = nil
	c.MessageContextInfo = nil
	return proto.Size(c) == 0
}

func (e *Engine) onHistory(ev *events.HistorySync) {
	cli := e.client()
	data := ev.Data
	for _, p := range data.GetPushnames() {
		if p.GetPushname() != "" {
			if jid, err := types.ParseJID(p.GetID()); err == nil {
				e.emit(engine.ContactEvent{Chat: msgstore.ChatInfo{ID: jid.ToNonAD().String(), Name: p.GetPushname()}})
			}
		}
	}
	for _, conv := range data.GetConversations() {
		jid, err := types.ParseJID(conv.GetID())
		if err != nil {
			continue
		}
		if jid.Server == types.GroupServer {
			if n := firstNonEmpty(conv.GetName(), conv.GetDisplayName()); n != "" {
				e.mu.Lock()
				e.groupName[jid.String()] = n
				e.mu.Unlock()
			}
		}
		for _, hm := range conv.GetMessages() {
			wm := hm.GetMessage()
			if wm == nil {
				continue
			}
			msg, err := cli.ParseWebMessage(jid, wm)
			if err != nil {
				continue
			}
			e.onMessage(msg, true)
		}
	}
}
