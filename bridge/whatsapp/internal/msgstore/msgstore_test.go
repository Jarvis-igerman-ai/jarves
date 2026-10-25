package msgstore

import (
	"context"
	"testing"

	"github.com/Jarvis-igerman-ai/jarves/bridge/whatsapp/internal/memdb"
)

func TestStore(t *testing.T) {
	ctx := context.Background()
	db, err := memdb.Open(ctx, nil)
	if err != nil {
		t.Fatal(err)
	}
	s, err := New(ctx, db.DB)
	if err != nil {
		t.Fatal(err)
	}
	a := "972541112222@s.whatsapp.net"
	_ = s.UpsertChat(ctx, ChatInfo{ID: a, Name: "Dana", Number: "+972541112222"})
	_ = s.UpsertChat(ctx, ChatInfo{ID: a}) // empty keeps name
	_ = s.AddMessage(ctx, Message{ID: "1", Chat: a, TS: 100, Kind: "text", Text: "Shalom DANA"}, true, true)
	_ = s.AddMessage(ctx, Message{ID: "2", Chat: a, TS: 200, Kind: "image", Media: &Media{Mime: "image/jpeg", Size: 9, Caption: "beach"}}, true, true)
	_ = s.AddMessage(ctx, Message{ID: "3", Chat: a, TS: 300, Kind: "text", Text: "mine", FromMe: true}, true, true)
	_ = s.AddMessage(ctx, Message{ID: "4", Chat: "1@s.whatsapp.net", TS: 50, Kind: "text", Text: "dana"}, false, false)
	c, ok, _ := s.GetChat(ctx, a)
	if !ok || c.Name != "Dana" || *c.Number != "+972541112222" || c.Unread != 2 || c.LastTS != 300 || !c.HasHistory {
		t.Fatalf("%+v", c)
	}
	if !s.HasHistory(ctx, "1@s.whatsapp.net") || s.HasHistory(ctx, "2@s.whatsapp.net") {
		t.Fatal("history flag")
	}
	ms, _ := s.Messages(ctx, a, 0, 0, 2)
	if len(ms) != 2 || ms[0].ID != "2" || ms[1].ID != "3" || ms[0].Media == nil || ms[0].Media.Caption != "beach" {
		t.Fatalf("%+v", ms)
	}
	found, _ := s.Search(ctx, "dana", func(string) bool { return true }, 0, 10)
	if len(found) != 1 || found[0].ID != "1" {
		t.Fatalf("search %+v", found)
	}
	found, _ = s.Search(ctx, "BEACH", func(string) bool { return false }, 0, 10)
	if len(found) != 0 {
		t.Fatal("unreadable leaked")
	}
	_ = s.EditMessage(ctx, a, "1", "edited")
	_ = s.DeleteMessage(ctx, a, "2")
	ms, _ = s.Messages(ctx, a, 0, 0, 10)
	if !ms[0].Edited || ms[0].Text != "edited" || !ms[1].Deleted || ms[1].Media.Caption != "" {
		t.Fatalf("%+v", ms)
	}
	before := 250.0
	if n, _ := s.Purge(ctx, &before); n != 2 {
		t.Fatalf("purge %d", n)
	}
	if !s.HasHistory(ctx, a) {
		t.Fatal("history must survive purge")
	}
	_ = s.Seen(ctx, a)
	c, _, _ = s.GetChat(ctx, a)
	if c.Unread != 0 {
		t.Fatal("seen")
	}
	if n, _ := s.Purge(ctx, nil); n != 1 {
		t.Fatalf("purge all %d", n)
	}
	_ = s.Wipe(ctx)
	if all, _ := s.AllChats(ctx); len(all) != 0 {
		t.Fatal("wipe")
	}
}
