package main

import "testing"

func TestFlags(t *testing.T) {
	if run([]string{"--version"}) != 0 {
		t.Fatal("--version")
	}
	if run(nil) != 2 {
		t.Fatal("missing --state-dir must exit 2")
	}
	if run([]string{"--state-dir", t.TempDir(), "--port", "70000"}) != 2 {
		t.Fatal("bad port")
	}
	if run([]string{"--nope"}) != 2 {
		t.Fatal("unknown flag")
	}
}
