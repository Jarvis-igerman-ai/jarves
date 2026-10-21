package securestore

import (
	"bytes"
	"os"
	"path/filepath"
	"testing"
)

func TestSealOpenRoundTripAndFormat(t *testing.T) {
	key := bytes.Repeat([]byte{7}, 32)
	pt := []byte("SQLite format 3\x00 ... device keys ...")
	blob, err := Seal(key, pt)
	if err != nil {
		t.Fatal(err)
	}
	if string(blob[:4]) != "JWS1" || len(blob) != 4+12+len(pt)+16 {
		t.Fatalf("format: magic %q len %d", blob[:4], len(blob))
	}
	if bytes.Contains(blob, []byte("device keys")) {
		t.Fatal("plaintext visible")
	}
	got, err := Open(key, blob)
	if err != nil || !bytes.Equal(got, pt) {
		t.Fatalf("round trip: %v", err)
	}
	blob2, _ := Seal(key, pt)
	if bytes.Equal(blob[4:16], blob2[4:16]) {
		t.Fatal("nonce reused")
	}
}

func TestTamperAndWrongKeyFail(t *testing.T) {
	key := bytes.Repeat([]byte{7}, 32)
	blob, _ := Seal(key, []byte("secret"))
	for i := range blob {
		b := append([]byte(nil), blob...)
		b[i] ^= 0x01
		if _, err := Open(key, b); err != ErrLocked {
			t.Fatalf("byte %d tamper not detected", i)
		}
	}
	other := bytes.Repeat([]byte{8}, 32)
	if _, err := Open(other, blob); err != ErrLocked {
		t.Fatal("wrong key opened")
	}
	if _, err := Open(key, blob[:10]); err != ErrLocked {
		t.Fatal("short blob")
	}
	if _, err := Seal(key[:16], nil); err == nil {
		t.Fatal("short key accepted")
	}
}

func TestKeyFileLifecycle(t *testing.T) {
	dir := t.TempDir()
	k1, err := LoadOrCreateKey(dir)
	if err != nil || len(k1) != 32 {
		t.Fatal(err)
	}
	raw, _ := os.ReadFile(filepath.Join(dir, KeyFile))
	if Protection() == "dpapi" && bytes.Contains(raw, k1) {
		t.Fatal("key stored unwrapped")
	}
	k2, err := LoadOrCreateKey(dir)
	if err != nil || !bytes.Equal(k1, k2) {
		t.Fatal("key not stable")
	}
	if err := SaveStore(dir, k1, []byte("db bytes")); err != nil {
		t.Fatal(err)
	}
	pt, ok, err := LoadStore(dir, k2)
	if err != nil || !ok || string(pt) != "db bytes" {
		t.Fatalf("load: %v %v", ok, err)
	}
	// corrupt wrapped key -> locked, nothing overwritten
	_ = os.WriteFile(filepath.Join(dir, KeyFile), []byte("garbage-garbage-garbage"), 0o600)
	if _, err := LoadOrCreateKey(dir); err != ErrLocked {
		t.Fatalf("want locked, got %v", err)
	}
	// store.enc without store.key -> locked (never a fresh key that would orphan the store)
	_ = os.Remove(filepath.Join(dir, KeyFile))
	if _, err := LoadOrCreateKey(dir); err != ErrLocked {
		t.Fatalf("want locked, got %v", err)
	}
	if _, err := os.Stat(filepath.Join(dir, KeyFile)); !os.IsNotExist(err) {
		t.Fatal("key file recreated")
	}
}

func TestAtomicWriteLeavesNoTemp(t *testing.T) {
	dir := t.TempDir()
	p := filepath.Join(dir, "x.json")
	for i := 0; i < 3; i++ {
		if err := WriteFileAtomic(p, []byte{byte(i)}); err != nil {
			t.Fatal(err)
		}
	}
	ents, _ := os.ReadDir(dir)
	if len(ents) != 1 {
		t.Fatalf("leftovers: %d", len(ents))
	}
}

func TestRestrictDir(t *testing.T) {
	d := filepath.Join(t.TempDir(), "whatsapp_bridge")
	if err := RestrictDir(d); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(d, "f"), []byte("x"), 0o600); err != nil {
		t.Fatal(err)
	}
}
