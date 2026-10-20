// Package policy is the pure send-policy layer of the JARVES WhatsApp bridge (BridgeProtocol v1, PROTOCOL.md
// section 4). It has no I/O, no clock and no network: every input (time, local hour, sent history, used nonces) is
// passed in, so the shared table testdata/policy_table.json runs identically here and in the Python fake.
package policy

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"math"
	"regexp"
	"sort"
	"strings"
	"unicode/utf8"
)

// Mode is the bridge mode (section 5).
type Mode string

const (
	ModeOff  Mode = "off"
	ModeRead Mode = "read"
	ModeSend Mode = "send"
)

// ValidMode reports whether m is one of off/read/send.
func ValidMode(m string) bool {
	return m == string(ModeOff) || m == string(ModeRead) || m == string(ModeSend)
}

// Fixed limits of v1 (section 4 rule 9).
const (
	MaxChars          = 1500
	MaxApprovalAhead  = 300.0 // seconds
	Window15s         = 15.0
	WindowHour        = 3600.0
	WindowDay         = 86400.0
	LimitPer15s       = 1
	LimitPerHour      = 10
	LimitPerDay       = 30
	LimitRecipientsHr = 3
	ApprovalPrefix    = "wa1."
)

// Limits is reported by GET /status (rate.limits).
var Limits = map[string]int{
	"per_15s": LimitPer15s, "per_hour": LimitPerHour, "per_day": LimitPerDay, "recipients_per_hour": LimitRecipientsHr,
}

// Sent is one message that was really sent. To is any stable recipient key (the bridge uses the chat hash, the table
// uses the JID); only equality matters.
type Sent struct {
	TS float64
	To string
}

// State is everything the policy needs to know about the bridge at decision time.
type State struct {
	Killed     bool
	Mode       Mode
	LocalHour  int
	SendAllow  map[string]bool
	HasHistory func(jid string) bool
	Sent       []Sent
	UsedNonce  func(nonce string) bool
	Connected  bool
	// RecipientKey maps a JID to the key used in Sent (nil = identity).
	RecipientKey func(jid string) string
}

// Request is one POST /send body.
type Request struct {
	To         string `json:"to"`
	Text       string `json:"text"`
	ApprovalID string `json:"approval_id"`
}

// Payload is the signed JSON inside an approval id.
type Payload struct {
	V     int     `json:"v"`
	N     string  `json:"n"`
	To    string  `json:"to"`
	H     string  `json:"h"`
	Exp   float64 `json:"exp"`
	Night bool    `json:"night"`
}

// Decision is the result of Check. OK=true means "send now, then consume Approval.N".
type Decision struct {
	OK          bool
	Code        string
	HTTP        int
	Limit       string
	RetryAfterS int
	DaysLeft    int
	Approval    *Payload
}

func refuse(http int, code string) Decision { return Decision{HTTP: http, Code: code} }

var (
	reDirect = regexp.MustCompile(`^[0-9]{1,20}@(s\.whatsapp\.net|lid)$`)
	reNonce  = regexp.MustCompile(`^[0-9a-f]{32}$`)
	reHash   = regexp.MustCompile(`^[0-9a-f]{64}$`)
)

// IsGroup reports whether jid is a group JID.
func IsGroup(jid string) bool { return strings.HasSuffix(jid, "@g.us") }

// IsDirect reports whether jid is a plain person JID (phone or LID, no device part).
func IsDirect(jid string) bool { return reDirect.MatchString(jid) }

// TextHash is the approval `h`: sha256 hex of UTF-8 (to + "\n" + text).
func TextHash(to, text string) string {
	s := sha256.Sum256([]byte(to + "\n" + text))
	return hex.EncodeToString(s[:])
}

var b64 = base64.RawURLEncoding

// Sign mints an approval id (used by tests and documented for the Core approvals service).
func Sign(key []byte, p Payload) string {
	raw, _ := json.Marshal(struct {
		V     int    `json:"v"`
		N     string `json:"n"`
		To    string `json:"to"`
		H     string `json:"h"`
		Exp   int64  `json:"exp"`
		Night bool   `json:"night"`
	}{p.V, p.N, p.To, p.H, int64(p.Exp), p.Night})
	body := ApprovalPrefix + b64.EncodeToString(raw)
	return body + "." + b64.EncodeToString(mac(key, body))
}

func mac(key []byte, s string) []byte {
	m := hmac.New(sha256.New, key)
	m.Write([]byte(s))
	return m.Sum(nil)
}

// VerifyApproval checks format and signature (over the exact received string, before JSON parsing) and the payload
// shape. It does NOT check expiry, recipient, text or reuse. ok=false means WA_APPROVAL_INVALID.
func VerifyApproval(key []byte, id string) (*Payload, bool) {
	if !strings.HasPrefix(id, ApprovalPrefix) || len(id) > 4096 {
		return nil, false
	}
	rest := id[len(ApprovalPrefix):]
	dot := strings.IndexByte(rest, '.')
	if dot <= 0 || dot == len(rest)-1 || strings.IndexByte(rest[dot+1:], '.') >= 0 {
		return nil, false
	}
	b64Payload, b64Sig := rest[:dot], rest[dot+1:]
	sig, err := b64.DecodeString(b64Sig)
	if err != nil || len(sig) != sha256.Size || !b64Canonical(b64Sig, sig) {
		return nil, false
	}
	if !hmac.Equal(sig, mac(key, ApprovalPrefix+b64Payload)) {
		return nil, false
	}
	raw, err := b64.DecodeString(b64Payload)
	if err != nil || !utf8.Valid(raw) {
		return nil, false
	}
	var m map[string]json.RawMessage
	if json.Unmarshal(raw, &m) != nil {
		return nil, false
	}
	var p Payload
	if !jsonInt(m["v"], &p.V) || p.V != 1 || !jsonString(m["n"], &p.N) || !reNonce.MatchString(p.N) ||
		!jsonString(m["to"], &p.To) || p.To == "" || !jsonString(m["h"], &p.H) || !reHash.MatchString(p.H) ||
		!jsonNumber(m["exp"], &p.Exp) {
		return nil, false
	}
	if nb, ok := m["night"]; ok {
		if json.Unmarshal(nb, &p.Night) != nil {
			return nil, false
		}
	}
	return &p, true
}

func b64Canonical(s string, b []byte) bool { return b64.EncodeToString(b) == s }

func jsonString(r json.RawMessage, out *string) bool {
	return r != nil && len(r) > 0 && r[0] == '"' && json.Unmarshal(r, out) == nil
}

func jsonNumber(r json.RawMessage, out *float64) bool {
	if r == nil || len(r) == 0 || !(r[0] == '-' || (r[0] >= '0' && r[0] <= '9')) {
		return false
	}
	return json.Unmarshal(r, out) == nil && !math.IsNaN(*out) && !math.IsInf(*out, 0)
}

func jsonInt(r json.RawMessage, out *int) bool {
	var f float64
	if !jsonNumber(r, &f) || f != math.Trunc(f) {
		return false
	}
	*out = int(f)
	return true
}

// QuietHour reports whether a local hour is inside the quiet window 23:00-06:59.
func QuietHour(h int) bool { return h >= 23 || h < 7 }

// Check applies the normative order of PROTOCOL.md section 4. First failing rule wins.
func Check(now float64, key []byte, st State, req Request) Decision {
	// 1. kill
	if st.Killed {
		return refuse(423, "WA_KILLED")
	}
	// 2. mode
	switch st.Mode {
	case ModeSend:
	case ModeRead:
		return refuse(409, "WA_MODE_READ")
	default:
		return refuse(409, "WA_MODE_OFF")
	}
	// 3. text
	if strings.TrimSpace(req.Text) == "" {
		return refuse(400, "WA_EMPTY")
	}
	if utf8.RuneCountInString(req.Text) > MaxChars {
		return refuse(400, "WA_TOO_LONG")
	}
	// 4. recipient shape
	if IsGroup(req.To) {
		return refuse(403, "WA_GROUP_REFUSED")
	}
	if !IsDirect(req.To) {
		return refuse(403, "WA_BAD_RECIPIENT")
	}
	// 5. allow-list
	if !st.SendAllow[req.To] {
		return refuse(403, "WA_NOT_ALLOWED")
	}
	// 6. history
	if st.HasHistory == nil || !st.HasHistory(req.To) {
		return refuse(403, "WA_NO_HISTORY")
	}
	// 7. approval
	if req.ApprovalID == "" {
		return refuse(403, "WA_APPROVAL_MISSING")
	}
	p, ok := VerifyApproval(key, req.ApprovalID)
	if !ok || p.Exp > now+MaxApprovalAhead {
		return refuse(403, "WA_APPROVAL_INVALID")
	}
	if p.Exp < now {
		return refuse(403, "WA_APPROVAL_EXPIRED")
	}
	if p.To != req.To || !hmac.Equal([]byte(p.H), []byte(TextHash(req.To, req.Text))) {
		return refuse(403, "WA_APPROVAL_MISMATCH")
	}
	if st.UsedNonce != nil && st.UsedNonce(p.N) {
		return refuse(403, "WA_APPROVAL_REUSED")
	}
	// 8. quiet hours
	if QuietHour(st.LocalHour) && !p.Night {
		return refuse(403, "WA_QUIET_HOURS")
	}
	// 9. rate limits
	keyOf := st.RecipientKey
	if keyOf == nil {
		keyOf = func(s string) string { return s }
	}
	if limit, retry := RateCheck(now, st.Sent, keyOf(req.To)); limit != "" {
		d := refuse(429, "WA_RATE_LIMITED")
		d.Limit, d.RetryAfterS = limit, retry
		return d
	}
	// 10. connection
	if !st.Connected {
		return refuse(503, "WA_NOT_CONNECTED")
	}
	return Decision{OK: true, HTTP: 200, Approval: p}
}

// RateCounts returns the counters reported by GET /status.
func RateCounts(now float64, sent []Sent) (last15s, hour, day, recipientsHour int) {
	seen := map[string]bool{}
	for _, s := range sent {
		age := now - s.TS
		if age < Window15s {
			last15s++
		}
		if age < WindowHour {
			hour++
			seen[s.To] = true
		}
		if age < WindowDay {
			day++
		}
	}
	return last15s, hour, day, len(seen)
}

// RateCheck returns the first exceeded limit name ("" = fine) and the seconds until it would pass. A send is inside a
// window when its age is strictly less than the window length.
func RateCheck(now float64, sent []Sent, to string) (string, int) {
	in := func(w float64) []float64 {
		var ts []float64
		for _, s := range sent {
			if now-s.TS < w {
				ts = append(ts, s.TS)
			}
		}
		sort.Float64s(ts)
		return ts
	}
	retry := func(ts []float64, limit int, w float64) int {
		// the oldest entry that must leave the window for the count to drop below limit
		t := ts[len(ts)-limit] + w - now
		return int(math.Max(1, math.Ceil(t)))
	}
	for _, r := range []struct {
		name  string
		w     float64
		limit int
	}{{"per_15s", Window15s, LimitPer15s}, {"per_hour", WindowHour, LimitPerHour}, {"per_day", WindowDay, LimitPerDay}} {
		if ts := in(r.w); len(ts) >= r.limit {
			return r.name, retry(ts, r.limit, r.w)
		}
	}
	latest := map[string]float64{}
	for _, s := range sent {
		if now-s.TS < WindowHour && s.To != to && s.TS > latest[s.To] {
			latest[s.To] = s.TS
		}
	}
	if len(latest) >= LimitRecipientsHr {
		var ts []float64
		for _, t := range latest {
			ts = append(ts, t)
		}
		sort.Float64s(ts)
		return "recipients_per_hour", retry(ts, LimitRecipientsHr, WindowHour)
	}
	return "", 0
}
