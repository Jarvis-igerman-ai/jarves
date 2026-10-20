package wm

import (
	"bytes"
	"context"
	"testing"
	"time"

	"go.mau.fi/whatsmeow/proto/waAdv"
	"go.mau.fi/whatsmeow/proto/waCommon"
	"go.mau.fi/whatsmeow/proto/waE2E"
	"go.mau.fi/whatsmeow/store"
	"go.mau.fi/whatsmeow/types"
	"go.mau.fi/whatsmeow/types/events"
	"google.golang.org/protobuf/proto"

	"github.com/igalgerman/jarves/bridge/whatsapp/internal/engine"
	"github.com/igalgerman/jarves/bridge/whatsapp/internal/memdb"
	"github.com/igalgerman/jarves/bridge/whatsapp/internal/securestore"
)

// No test here contacts WhatsApp: linking/connecting is BLOCKED until the owner links a device.

func TestNewUnlinkedEngineOnMemDB(t *testing.T) {
	ctx := context.Background()
	db, err := memdb.Open(ctx, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	e, err := New(ctx, db.DB)
	if err != nil {
		t.Fatal(err)
	}
	if e.Name() != "whatsmeow" || e.IsLoggedIn() || e.IsConnected() || e.State() != engine.StateNotLinked {
		t.Fatalf("state %s", e.State())
	}
	if store.DeviceProps.GetOs() != DeviceName {
		t.Fatalf("device name %q", store.DeviceProps.GetOs())
	}
	if _, _, err := e.SendText(ctx, "972541112222@s.whatsapp.net", "x"); err != engine.ErrNotConnected {
		t.Fatalf("send offline: %v", err)
	}
	if err := e.Connect(ctx); err == nil {
		t.Fatal("connect without a linked device")
	}
}

func TestDeviceKeysSurviveSealedSnapshot(t *testing.T) {
	ctx := context.Background()
	db, _ := memdb.Open(ctx, nil)
	e, err := New(ctx, db.DB)
	if err != nil {
		t.Fatal(err)
	}
	dev := e.container.NewDevice()
	dev.ID = &types.JID{User: "972500000000", Device: 7, Server: types.DefaultUserServer}
	dev.Account = &waAdv.ADVSignedDeviceIdentity{Details: []byte("details"), AccountSignature: make([]byte, 64),
		AccountSignatureKey: make([]byte, 32), DeviceSignature: make([]byte, 64)}
	if err := e.container.PutDevice(ctx, dev); err != nil {
		t.Fatal(err)
	}
	snap, err := db.Snapshot(ctx)
	if err != nil {
		t.Fatal(err)
	}
	key := bytes.Repeat([]byte{1}, 32)
	sealed, _ := securestore.Seal(key, snap)
	if bytes.Contains(sealed, dev.NoiseKey.Priv[:]) || bytes.Contains(sealed, []byte("972500000000")) {
		t.Fatal("sealed store leaks keys or numbers")
	}
	_ = db.Close()
	plain, err := securestore.Open(key, sealed)
	if err != nil {
		t.Fatal(err)
	}
	db2, err := memdb.Open(ctx, plain)
	if err != nil {
		t.Fatal(err)
	}
	defer db2.Close()
	e2, err := New(ctx, db2.DB)
	if err != nil {
		t.Fatal(err)
	}
	if !e2.IsLoggedIn() || e2.State() != engine.StateDisconnected {
		t.Fatalf("restored device: %s", e2.State())
	}
	got := e2.client().Store
	if got.ID.User != "972500000000" || !bytes.Equal(got.NoiseKey.Priv[:], dev.NoiseKey.Priv[:]) {
		t.Fatal("keys differ after restore")
	}
	// local logout without a connection deletes the keys and prepares a fresh unlinked device
	if err := e2.Logout(ctx); err == nil {
		t.Fatal("offline logout must report that the server-side logout was skipped")
	}
	if e2.IsLoggedIn() {
		t.Fatal("still linked")
	}
	if d, _ := e2.container.GetAllDevices(ctx); len(d) != 0 {
		t.Fatal("device not deleted")
	}
}

func TestClassify(t *testing.T) {
	cases := []struct {
		m    *waE2E.Message
		kind string
		text string
	}{
		{&waE2E.Message{Conversation: proto.String("שלום")}, "text", "שלום"},
		{&waE2E.Message{ExtendedTextMessage: &waE2E.ExtendedTextMessage{Text: proto.String("link")}}, "text", "link"},
		{&waE2E.Message{ImageMessage: &waE2E.ImageMessage{Mimetype: proto.String("image/jpeg"), FileLength: proto.Uint64(5), Caption: proto.String("c")}}, "image", ""},
		{&waE2E.Message{AudioMessage: &waE2E.AudioMessage{}}, "audio", ""},
		{&waE2E.Message{DocumentMessage: &waE2E.DocumentMessage{}}, "document", ""},
		{&waE2E.Message{StickerMessage: &waE2E.StickerMessage{}}, "sticker", ""},
		{&waE2E.Message{VideoMessage: &waE2E.VideoMessage{}}, "video", ""},
		{&waE2E.Message{LocationMessage: &waE2E.LocationMessage{}}, "location", ""},
		{&waE2E.Message{ContactMessage: &waE2E.ContactMessage{}}, "contact", ""},
		{&waE2E.Message{ReactionMessage: &waE2E.ReactionMessage{}}, "", ""},
		{&waE2E.Message{SenderKeyDistributionMessage: &waE2E.SenderKeyDistributionMessage{}}, "", ""},
		{&waE2E.Message{PollCreationMessage: &waE2E.PollCreationMessage{}}, "other", ""},
	}
	for i, c := range cases {
		k, txt, media := classify(c.m)
		if k != c.kind || txt != c.text {
			t.Fatalf("case %d: %s %q", i, k, txt)
		}
		if k == "image" && (media == nil || media.Mime != "image/jpeg" || media.Size != 5 || media.Caption != "c") {
			t.Fatal("media metadata")
		}
	}
}

func TestEventMapping(t *testing.T) {
	ctx := context.Background()
	db, _ := memdb.Open(ctx, nil)
	defer db.Close()
	e, _ := New(ctx, db.DB)
	var got []engine.Event
	e.SetHandler(func(ev engine.Event) { got = append(got, ev) })
	e.handle(&events.TemporaryBan{Code: events.TempBanSentToTooManyPeople})
	e.handle(&events.StreamReplaced{})
	e.handle(&events.ClientOutdated{})
	e.handle(&events.NotifyAccountReachoutTimelock{IsActive: true})
	e.handle(&events.LoggedOut{OnConnect: true, Reason: events.ConnectFailureLoggedOut})
	want := []engine.Event{
		engine.AccountWarningEvent{Code: engine.WarnTemporaryBan},
		engine.AccountWarningEvent{Code: engine.WarnStreamReplaced},
		engine.AccountWarningEvent{Code: engine.WarnClientOutdated},
		engine.AccountWarningEvent{Code: engine.WarnAccountRestricted},
		engine.ConnectionEvent{State: engine.StateLoggedOut},
		engine.LoggedOutEvent{Reason: "connect_failure_401"},
	}
	if len(got) != len(want) {
		t.Fatalf("%v", got)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Fatalf("%d: %v != %v", i, got[i], want[i])
		}
	}
	got = nil
	info := types.MessageInfo{MessageSource: types.MessageSource{Chat: types.NewJID("972541112222", types.DefaultUserServer),
		Sender: types.NewJID("972541112222", types.DefaultUserServer)}, ID: "ABC", PushName: "Dana", Timestamp: time.Unix(1790000000, 0)}
	e.handle(&events.Message{Info: info, Message: &waE2E.Message{Conversation: proto.String("hi")}})
	e.handle(&events.Message{Info: info, Message: &waE2E.Message{ProtocolMessage: &waE2E.ProtocolMessage{
		Type: waE2E.ProtocolMessage_REVOKE.Enum(), Key: &waCommon.MessageKey{ID: proto.String("OLD")}}}})
	status := info
	status.Chat = types.StatusBroadcastJID
	e.handle(&events.Message{Info: status, Message: &waE2E.Message{Conversation: proto.String("status")}})
	if len(got) != 2 {
		t.Fatalf("%#v", got)
	}
	m := got[0].(engine.MessageEvent)
	if m.Msg.Text != "hi" || m.Msg.Kind != "text" || m.Chat.Number != "+972541112222" || m.Chat.Name != "Dana" || m.Msg.TS != 1790000000 {
		t.Fatalf("%+v", m)
	}
	if r := got[1].(engine.RevokeEvent); r.ID != "OLD" {
		t.Fatal("revoke")
	}
}
