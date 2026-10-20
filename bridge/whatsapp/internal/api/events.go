package api

import (
	"context"
	"encoding/json"
	"net/http"
	"sync"
	"time"

	"github.com/coder/websocket"
)

// PingEvery is the WebSocket ping interval (section 7).
var PingEvery = 20 * time.Second

type client struct {
	ch     chan map[string]any
	closed chan struct{}
	once   sync.Once
}

func (c *client) close() { c.once.Do(func() { close(c.closed) }) }

type hub struct {
	mu      sync.Mutex
	clients map[*client]struct{}
}

func newHub() *hub { return &hub{clients: map[*client]struct{}{}} }

// broadcast queues ev for every client. The caller has already applied the read gate (chat = "" for global
// events). A client that cannot keep up (256 queued events) is disconnected rather than blocking the bridge.
func (h *hub) broadcast(_ string, ev map[string]any) {
	h.mu.Lock()
	defer h.mu.Unlock()
	for c := range h.clients {
		select {
		case c.ch <- ev:
		default:
			c.close()
		}
	}
}

func (h *hub) add() *client {
	c := &client{ch: make(chan map[string]any, 256), closed: make(chan struct{})}
	h.mu.Lock()
	h.clients[c] = struct{}{}
	h.mu.Unlock()
	return c
}

func (h *hub) remove(c *client) {
	h.mu.Lock()
	delete(h.clients, c)
	h.mu.Unlock()
	c.close()
}

func (h *hub) closeAll() {
	h.mu.Lock()
	defer h.mu.Unlock()
	for c := range h.clients {
		c.close()
	}
}

func (h *hub) count() int {
	h.mu.Lock()
	defer h.mu.Unlock()
	return len(h.clients)
}

// handleEvents serves GET /events (already authenticated). Frames: {"type", "seq", ...fields}; seq starts at 0 with
// the hello frame and grows by one per frame on this connection.
func (s *Server) handleEvents(w http.ResponseWriter, r *http.Request) {
	conn, err := websocket.Accept(w, r, &websocket.AcceptOptions{}) // same-origin only (library default)
	if err != nil {
		return
	}
	defer conn.CloseNow()
	conn.SetReadLimit(4096)
	c := s.hub.add()
	defer s.hub.remove(c)
	ctx := conn.CloseRead(r.Context())
	seq := 0
	write := func(ev map[string]any) error {
		ev["seq"] = seq
		seq++
		b, err := json.Marshal(ev)
		if err != nil {
			return err
		}
		wctx, cancel := context.WithTimeout(ctx, 10*time.Second)
		defer cancel()
		return conn.Write(wctx, websocket.MessageText, b)
	}
	if write(map[string]any{"type": "hello", "status": s.status(ctx)}) != nil {
		return
	}
	ping := time.NewTicker(PingEvery)
	defer ping.Stop()
	var stop <-chan struct{}
	if s.ctx != nil {
		stop = s.ctx.Done()
	}
	for {
		select {
		case <-ctx.Done():
			return
		case <-stop:
			_ = conn.Close(websocket.StatusGoingAway, "bridge stopping")
			return
		case <-c.closed:
			_ = conn.Close(websocket.StatusPolicyViolation, "too slow")
			return
		case ev := <-c.ch:
			cp := make(map[string]any, len(ev)+1)
			for k, v := range ev {
				cp[k] = v
			}
			if write(cp) != nil {
				return
			}
		case <-ping.C:
			pctx, cancel := context.WithTimeout(ctx, 10*time.Second)
			err := conn.Ping(pctx)
			cancel()
			if err != nil {
				return
			}
		}
	}
}
