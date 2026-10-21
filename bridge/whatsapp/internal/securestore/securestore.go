// Package securestore seals the bridge's in-memory database snapshot to store.enc (PROTOCOL.md section 6):
// "JWS1" magic + 12-byte nonce + AES-256-GCM(ciphertext + tag), additional data "jarves-wa-store-v1". The random
// 32-byte key lives in store.key, wrapped with Windows DPAPI (CurrentUser, entropy "jarves-wa-store-v1"); off Windows
// it is a plain 0600 file (key_protection "file").
package securestore

import (
	"bytes"
	"crypto/aes"
	"crypto/cipher"
	"crypto/rand"
	"errors"
	"fmt"
	"os"
	"path/filepath"
)

const (
	Magic     = "JWS1"
	AAD       = "jarves-wa-store-v1"
	Entropy   = "jarves-wa-store-v1"
	KeyFile   = "store.key"
	StoreFile = "store.enc"
	nonceSize = 12
)

// ErrLocked = the key cannot be unwrapped (other Windows user/PC) or the store does not decrypt (WA_STORE_LOCKED).
var ErrLocked = errors.New("WA_STORE_LOCKED")

// Seal encrypts plaintext with key (32 bytes).
func Seal(key, plaintext []byte) ([]byte, error) {
	aead, err := newAEAD(key)
	if err != nil {
		return nil, err
	}
	nonce := make([]byte, nonceSize)
	if _, err := rand.Read(nonce); err != nil {
		return nil, err
	}
	out := make([]byte, 0, len(Magic)+nonceSize+len(plaintext)+aead.Overhead())
	out = append(out, Magic...)
	out = append(out, nonce...)
	return aead.Seal(out, nonce, plaintext, []byte(AAD)), nil
}

// Open decrypts a Seal output. Any tampering, wrong key or wrong format returns ErrLocked.
func Open(key, blob []byte) ([]byte, error) {
	aead, err := newAEAD(key)
	if err != nil {
		return nil, err
	}
	if len(blob) < len(Magic)+nonceSize+aead.Overhead() || !bytes.Equal(blob[:len(Magic)], []byte(Magic)) {
		return nil, ErrLocked
	}
	nonce := blob[len(Magic) : len(Magic)+nonceSize]
	pt, err := aead.Open(nil, nonce, blob[len(Magic)+nonceSize:], []byte(AAD))
	if err != nil {
		return nil, ErrLocked
	}
	return pt, nil
}

func newAEAD(key []byte) (cipher.AEAD, error) {
	if len(key) != 32 {
		return nil, fmt.Errorf("store key must be 32 bytes")
	}
	block, err := aes.NewCipher(key)
	if err != nil {
		return nil, err
	}
	return cipher.NewGCM(block)
}

// Protection reports how store.key is protected on this OS: "dpapi" or "file".
func Protection() string { return protection }

// LoadOrCreateKey returns the store key from dir/store.key, creating a new random key when neither store.key nor
// store.enc exist. A store.enc without store.key, or a key that does not unwrap, is ErrLocked (nothing is
// overwritten).
func LoadOrCreateKey(dir string) ([]byte, error) {
	kp := filepath.Join(dir, KeyFile)
	wrapped, err := os.ReadFile(kp)
	if err == nil {
		key, err := unwrapKey(wrapped)
		if err != nil || len(key) != 32 {
			return nil, ErrLocked
		}
		return key, nil
	}
	if !os.IsNotExist(err) {
		return nil, err
	}
	if _, err := os.Stat(filepath.Join(dir, StoreFile)); err == nil {
		return nil, ErrLocked
	}
	key := make([]byte, 32)
	if _, err := rand.Read(key); err != nil {
		return nil, err
	}
	w, err := wrapKey(key)
	if err != nil {
		return nil, err
	}
	if err := WriteFileAtomic(kp, w); err != nil {
		return nil, err
	}
	return key, nil
}

// LoadStore reads and decrypts dir/store.enc. ok=false (nil error) when there is no store yet.
func LoadStore(dir string, key []byte) ([]byte, bool, error) {
	blob, err := os.ReadFile(filepath.Join(dir, StoreFile))
	if os.IsNotExist(err) {
		return nil, false, nil
	}
	if err != nil {
		return nil, false, err
	}
	pt, err := Open(key, blob)
	if err != nil {
		return nil, false, err
	}
	return pt, true, nil
}

// SaveStore seals plaintext and writes dir/store.enc atomically.
func SaveStore(dir string, key, plaintext []byte) error {
	blob, err := Seal(key, plaintext)
	if err != nil {
		return err
	}
	return WriteFileAtomic(filepath.Join(dir, StoreFile), blob)
}

// WriteFileAtomic writes data to a temp file in the same directory (0600), syncs it and renames it over path.
func WriteFileAtomic(path string, data []byte) error {
	dir := filepath.Dir(path)
	f, err := os.CreateTemp(dir, "."+filepath.Base(path)+".tmp*")
	if err != nil {
		return err
	}
	tmp := f.Name()
	ok := false
	defer func() {
		if !ok {
			_ = f.Close()
			_ = os.Remove(tmp)
		}
	}()
	if err := f.Chmod(0o600); err != nil && !isWindows {
		return err
	}
	if _, err := f.Write(data); err != nil {
		return err
	}
	if err := f.Sync(); err != nil {
		return err
	}
	if err := f.Close(); err != nil {
		return err
	}
	if err := os.Rename(tmp, path); err != nil {
		return err
	}
	ok = true
	return nil
}
