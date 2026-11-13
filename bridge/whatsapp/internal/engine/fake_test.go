package engine

import (
	"context"
	"testing"
)

func TestFakeRecordsHistoryRequests(t *testing.T) {
	f := NewFake(true)
	a := HistoryAnchor{ID: "M1", TS: 10}
	if err := f.RequestHistory(context.Background(), "r1", "c@s.whatsapp.net", a, 50); err != ErrNotConnected {
		t.Fatalf("offline err %v", err)
	}
	_ = f.Connect(context.Background())
	if err := f.RequestHistory(context.Background(), f.NewRequestID(), "c@s.whatsapp.net", a, 50); err != nil {
		t.Fatal(err)
	}
	if len(f.HistoryRequests) != 1 || f.HistoryRequests[0] != (FakeHistoryRequest{ID: "hist1", Chat: "c@s.whatsapp.net", Anchor: a, Count: 50}) {
		t.Fatalf("requests %+v", f.HistoryRequests)
	}
}
