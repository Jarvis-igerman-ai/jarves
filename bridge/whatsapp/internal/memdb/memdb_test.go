package memdb

import (
	"context"
	"testing"
)

func TestSnapshotRestoreAndChanges(t *testing.T) {
	ctx := context.Background()
	db, err := Open(ctx, nil)
	if err != nil {
		t.Fatal(err)
	}
	c0, _ := db.Changes(ctx)
	if _, err := db.Exec("CREATE TABLE t (a TEXT)"); err != nil {
		t.Fatal(err)
	}
	if _, err := db.Exec("INSERT INTO t VALUES ('שלום'), ('x')"); err != nil {
		t.Fatal(err)
	}
	c1, _ := db.Changes(ctx)
	if c1 == c0 {
		t.Fatal("changes did not move")
	}
	snap, err := db.Snapshot(ctx)
	if err != nil || len(snap) < 512 || string(snap[:15]) != "SQLite format 3" {
		t.Fatalf("snapshot: %v %d", err, len(snap))
	}
	_ = db.Close()
	db2, err := Open(ctx, snap)
	if err != nil {
		t.Fatal(err)
	}
	defer db2.Close()
	var n int
	var fk int
	_ = db2.QueryRow("SELECT count(*) FROM t WHERE a = 'שלום'").Scan(&n)
	_ = db2.QueryRow("PRAGMA foreign_keys").Scan(&fk)
	if n != 1 || fk != 1 {
		t.Fatalf("restored: %d fk %d", n, fk)
	}
	// writes after restore work (resizeable image)
	if _, err := db2.Exec("INSERT INTO t SELECT a FROM t"); err != nil {
		t.Fatal(err)
	}
	if _, err := Open(ctx, []byte("not a database at all, definitely not")); err == nil {
		t.Fatal("garbage restored")
	}
}
