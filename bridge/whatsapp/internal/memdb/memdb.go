// Package memdb holds the bridge's single SQLite database (whatsmeow device store + contacts + messages) purely in
// memory (pure-Go modernc.org/sqlite, CGO_ENABLED=0). Plaintext never touches the disk: the database is serialized to
// a byte slice (sqlite3_serialize) and sealed by internal/securestore; at start it is restored with sqlite3_deserialize.
//
// The pool is pinned to ONE connection that never expires, because an in-memory database lives exactly as long as its
// connection. (This replaces "shared cache": with a single connection there is nothing to share, and deserialize into
// a shared-cache database is not supported by SQLite.)
package memdb

import (
	"context"
	"database/sql"
	"errors"
	"fmt"

	_ "modernc.org/sqlite" // registers the "sqlite" driver
)

type serializer interface {
	Serialize() ([]byte, error)
	Deserialize([]byte) error
}

// DB is the in-memory database.
type DB struct {
	*sql.DB
}

// Open creates an empty in-memory database. When snapshot is non-empty it is deserialized into it first.
func Open(ctx context.Context, snapshot []byte) (*DB, error) {
	db, err := sql.Open("sqlite", "file::memory:?_txlock=immediate")
	if err != nil {
		return nil, err
	}
	db.SetMaxOpenConns(1)
	db.SetMaxIdleConns(1)
	db.SetConnMaxLifetime(0)
	db.SetConnMaxIdleTime(0)
	m := &DB{DB: db}
	if len(snapshot) > 0 {
		if err := m.raw(ctx, func(s serializer) error { return s.Deserialize(snapshot) }); err != nil {
			_ = db.Close()
			return nil, fmt.Errorf("restore: %w", err)
		}
	}
	if _, err := db.ExecContext(ctx, "PRAGMA foreign_keys = ON"); err != nil {
		_ = db.Close()
		return nil, err
	}
	// quick sanity check that the restored image is a database
	if _, err := db.ExecContext(ctx, "SELECT count(*) FROM sqlite_master"); err != nil {
		_ = db.Close()
		return nil, fmt.Errorf("restore check: %w", err)
	}
	return m, nil
}

func (m *DB) raw(ctx context.Context, f func(serializer) error) error {
	c, err := m.DB.Conn(ctx)
	if err != nil {
		return err
	}
	defer c.Close()
	return c.Raw(func(dc any) error {
		s, ok := dc.(serializer)
		if !ok {
			return errors.New("driver cannot serialize")
		}
		return f(s)
	})
}

// Snapshot returns the serialized database image.
func (m *DB) Snapshot(ctx context.Context) ([]byte, error) {
	var out []byte
	err := m.raw(ctx, func(s serializer) error {
		b, err := s.Serialize()
		out = b
		return err
	})
	return out, err
}

// Changes returns sqlite total_changes() of the (single) connection: it grows with every INSERT/UPDATE/DELETE, so a
// different value than at the last seal means the store is dirty.
func (m *DB) Changes(ctx context.Context) (int64, error) {
	var n int64
	err := m.QueryRowContext(ctx, "SELECT total_changes()").Scan(&n)
	return n, err
}
