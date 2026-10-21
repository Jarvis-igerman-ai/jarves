//go:build windows

package securestore

import (
	"bytes"
	"testing"
)

func TestDPAPIRoundTripAndWrongEntropy(t *testing.T) {
	key := bytes.Repeat([]byte{0x42}, 32)
	w, err := wrapKey(key)
	if err != nil {
		t.Fatal(err)
	}
	if bytes.Contains(w, key) {
		t.Fatal("DPAPI output contains the key")
	}
	got, err := unwrapKey(w)
	if err != nil || !bytes.Equal(got, key) {
		t.Fatalf("unwrap: %v", err)
	}
	if _, err := unprotect(w, []byte("other-entropy")); err == nil {
		t.Fatal("wrong entropy unwrapped")
	}
	bad := append([]byte(nil), w...)
	bad[len(bad)/2] ^= 0xff
	if _, err := unwrapKey(bad); err == nil {
		t.Fatal("tampered DPAPI blob unwrapped")
	}
	if Protection() != "dpapi" {
		t.Fatal("protection")
	}
}
