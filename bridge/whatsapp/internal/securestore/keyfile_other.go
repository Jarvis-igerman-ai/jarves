//go:build !windows

package securestore

import (
	"errors"
	"os"
)

// Off Windows (the Pi, later) the store key is a plain 0600 file: documented, unverified (PROTOCOL.md section 6).
const (
	protection = "file"
	isWindows  = false
)

func wrapKey(key []byte) ([]byte, error) { return append([]byte(nil), key...), nil }

func unwrapKey(w []byte) ([]byte, error) {
	if len(w) != 32 {
		return nil, errors.New("bad key file")
	}
	return append([]byte(nil), w...), nil
}

// RestrictDir creates dir with mode 0700.
func RestrictDir(dir string) error {
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return err
	}
	return os.Chmod(dir, 0o700)
}
