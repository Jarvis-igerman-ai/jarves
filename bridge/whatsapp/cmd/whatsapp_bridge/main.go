// Command whatsapp_bridge is the JARVES WhatsApp bridge (BridgeProtocol v1, bridge/whatsapp/PROTOCOL.md): a
// whatsmeow linked device behind a loopback-only, token-protected API that enforces the send policy by itself.
//
//	whatsapp_bridge --state-dir <JARVES state dir> [--port 0]
//	whatsapp_bridge --version
//
// State lives in <state-dir>/whatsapp_bridge. Nothing secret is ever printed: tokens and keys stay in their 0600
// files; logs (stderr, JSON lines) hold codes and counts only.
package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"os"
	"os/signal"
	"path/filepath"
	"syscall"

	"github.com/Jarvis-igerman-ai/jarves/bridge/whatsapp/internal/api"
	"github.com/Jarvis-igerman-ai/jarves/bridge/whatsapp/internal/engine/wm"
	"github.com/Jarvis-igerman-ai/jarves/bridge/whatsapp/internal/memdb"
	"github.com/Jarvis-igerman-ai/jarves/bridge/whatsapp/internal/msgstore"
	"github.com/Jarvis-igerman-ai/jarves/bridge/whatsapp/internal/securestore"
)

// version is set at build time: -ldflags "-X main.version=<v>".
var version = "dev"

func main() { os.Exit(run(os.Args[1:])) }

func run(args []string) int {
	fs := flag.NewFlagSet("whatsapp_bridge", flag.ContinueOnError)
	stateDir := fs.String("state-dir", "", "JARVES state directory (the bridge uses <state-dir>/whatsapp_bridge)")
	port := fs.Int("port", 0, "loopback port (0 = random; the port is written to endpoint.json)")
	showVersion := fs.Bool("version", false, "print the version and exit")
	if err := fs.Parse(args); err != nil {
		return 2
	}
	if *showVersion {
		fmt.Printf("whatsapp_bridge %s (whatsmeow %s, protocol v%d)\n", version, wm.Version, api.ProtocolV)
		return 0
	}
	if *stateDir == "" {
		fmt.Fprintln(os.Stderr, "whatsapp_bridge: --state-dir is required")
		return 2
	}
	if *port < 0 || *port > 65535 {
		fmt.Fprintln(os.Stderr, "whatsapp_bridge: --port must be 0..65535")
		return 2
	}
	log := api.NewLogger(nil)
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	dir := filepath.Join(*stateDir, "whatsapp_bridge")
	if err := securestore.RestrictDir(dir); err != nil {
		log.Error("WA_STATE_DIR", "The state directory cannot be created.", nil)
		return 1
	}
	locked := false
	var snapshot []byte
	key, err := securestore.LoadOrCreateKey(dir)
	if errors.Is(err, securestore.ErrLocked) {
		locked = true
	} else if err != nil {
		log.Error("WA_STORE_KEY", "The store key cannot be created or read.", nil)
		return 1
	} else {
		pt, ok, err := securestore.LoadStore(dir, key)
		switch {
		case errors.Is(err, securestore.ErrLocked):
			locked = true
		case err != nil:
			log.Error("WA_STORE_READ", "store.enc cannot be read.", nil)
			return 1
		case ok:
			snapshot = pt
		}
	}
	if locked {
		// never overwrite a store this user cannot open: serve status only, sealing disabled
		log.Error("WA_STORE_LOCKED", "The encrypted store cannot be opened by this Windows user; it is left untouched.", nil)
	}
	db, err := memdb.Open(ctx, snapshot)
	if err != nil {
		log.Error("WA_STORE_CORRUPT", "The decrypted store is not a valid database.", nil)
		return 1
	}
	defer db.Close()
	st, err := msgstore.New(ctx, db.DB)
	if err != nil {
		log.Error("WA_STORE_INIT", "The message store cannot be initialised.", nil)
		return 1
	}
	eng, err := wm.New(ctx, db.DB)
	if err != nil {
		log.Error("WA_ENGINE_INIT", "The WhatsApp engine cannot be initialised.", nil)
		return 1
	}
	seal := func(c context.Context) error {
		snap, err := db.Snapshot(c)
		if err != nil {
			return err
		}
		return securestore.SaveStore(dir, key, snap)
	}
	srv, err := api.New(api.Config{Dir: dir, Version: version, Engine: eng, Store: st, Changes: db.Changes,
		Seal: seal, StoreLocked: locked, Log: log})
	if err != nil {
		log.Error("WA_INIT", "The bridge cannot start.", nil)
		return 1
	}
	log.Info("WA_START", "Bridge starting.", api.F{"version": version, "store_locked": locked,
		"key_protection": securestore.Protection()})
	if err := srv.ListenAndServe(ctx, *port); err != nil {
		log.Error("WA_SERVE", "The bridge stopped with an error.", nil)
		return 1
	}
	return 0
}
