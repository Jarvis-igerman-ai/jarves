package api

import (
	"bufio"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"os"
	"path/filepath"
	"regexp"
	"strings"

	"github.com/igalgerman/jarves/bridge/whatsapp/internal/securestore"
)

// File names inside <state_dir>/whatsapp_bridge (PROTOCOL.md section 1).
const (
	FileToken     = "token"
	FileAdmin     = "admin.token"
	FileApproval  = "approval.key"
	FileEndpoint  = "endpoint.json"
	FilePolicy    = "policy.json"
	FileKill      = "KILL"
	FileAudit     = "audit.jsonl"
	FileNonces    = "nonces.json"
	ProtocolV     = 1
	WatchDays     = 14
	DefaultRetain = 14
)

var (
	reUserToken  = regexp.MustCompile(`^wab_[A-Za-z0-9_-]{43}$`)
	reAdminToken = regexp.MustCompile(`^waa_[A-Za-z0-9_-]{43}$`)
	reKeyHex     = regexp.MustCompile(`^[0-9a-f]{64}$`)
)

func random32() []byte {
	b := make([]byte, 32)
	if _, err := rand.Read(b); err != nil {
		panic("crypto/rand failed")
	}
	return b
}

// loadOrCreate reads path; when it is missing or does not match re, it writes gen() (0600, atomic).
func loadOrCreate(path string, re *regexp.Regexp, gen func() string) (string, bool, error) {
	if b, err := os.ReadFile(path); err == nil {
		if s := strings.TrimSpace(string(b)); re.MatchString(s) {
			return s, false, nil
		}
	} else if !os.IsNotExist(err) {
		return "", false, err
	}
	s := gen()
	return s, true, securestore.WriteFileAtomic(path, []byte(s))
}

func newToken(prefix string) func() string {
	return func() string { return prefix + base64.RawURLEncoding.EncodeToString(random32()) }
}

// ChatHash = first 16 hex of sha256(jid): the only chat identifier that may appear in audit lines and logs.
func ChatHash(jid string) string {
	s := sha256.Sum256([]byte(jid))
	return hex.EncodeToString(s[:])[:16]
}

func sha256Hex(s string) string {
	h := sha256.Sum256([]byte(s))
	return hex.EncodeToString(h[:])
}

// Warning is the last account warning.
type Warning struct {
	Code string  `json:"code"`
	At   float64 `json:"at"`
}

// ReadScope is the default read scope.
type ReadScope struct {
	AllDirect bool `json:"all_direct"`
	Groups    bool `json:"groups"`
}

// Policy is policy.json (section 5). warnings_total and account_warning are bridge additions so a restart does not
// forget a warning.
type Policy struct {
	V                 int       `json:"v"`
	Mode              string    `json:"mode"`
	ReadScope         ReadScope `json:"read_scope"`
	ReadAllow         []string  `json:"read_allow"`
	SendAllow         []string  `json:"send_allow"`
	RetentionDays     int       `json:"retention_days"`
	KeepNothing       bool      `json:"keep_nothing"`
	ReadSince         *float64  `json:"read_since"`
	WarningsSinceRead int       `json:"warnings_since_read"`
	WarningsTotal     int       `json:"warnings_total"`
	AccountWarning    *Warning  `json:"account_warning"`
}

func defaultPolicy() Policy {
	return Policy{V: 1, Mode: "off", ReadScope: ReadScope{AllDirect: true}, ReadAllow: []string{}, SendAllow: []string{},
		RetentionDays: DefaultRetain}
}

func loadPolicy(path string) (Policy, error) {
	p := defaultPolicy()
	b, err := os.ReadFile(path)
	if os.IsNotExist(err) {
		return p, nil
	}
	if err != nil {
		return p, err
	}
	if err := json.Unmarshal(b, &p); err != nil {
		return defaultPolicy(), err
	}
	if p.ReadAllow == nil {
		p.ReadAllow = []string{}
	}
	if p.SendAllow == nil {
		p.SendAllow = []string{}
	}
	if p.Mode != "off" && p.Mode != "read" && p.Mode != "send" {
		p.Mode = "off"
	}
	if p.RetentionDays < 1 || p.RetentionDays > 90 {
		p.RetentionDays = DefaultRetain
	}
	p.V = 1
	return p, nil
}

func savePolicy(path string, p Policy) error {
	b, err := json.MarshalIndent(p, "", "  ")
	if err != nil {
		return err
	}
	return securestore.WriteFileAtomic(path, append(b, '\n'))
}

// AuditLine is one audit.jsonl line: hashes and counts only.
type AuditLine struct {
	TS         float64 `json:"ts"`
	ChatHash   string  `json:"chat_hash"`
	TextSHA256 string  `json:"text_sha256"`
	Chars      int     `json:"chars"`
	Result     string  `json:"result"`
}

func appendAudit(path string, l AuditLine) error {
	f, err := os.OpenFile(path, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o600)
	if err != nil {
		return err
	}
	defer f.Close()
	b, _ := json.Marshal(l)
	_, err = f.Write(append(b, '\n'))
	return err
}

func readAudit(path string) []AuditLine {
	f, err := os.Open(path)
	if err != nil {
		return nil
	}
	defer f.Close()
	var out []AuditLine
	sc := bufio.NewScanner(f)
	sc.Buffer(make([]byte, 64*1024), 1024*1024)
	for sc.Scan() {
		var l AuditLine
		if json.Unmarshal(sc.Bytes(), &l) == nil {
			out = append(out, l)
		}
	}
	return out
}

type nonceFile struct {
	V    int                `json:"v"`
	Used map[string]float64 `json:"used"`
}

func loadNonces(path string, now float64) map[string]float64 {
	out := map[string]float64{}
	b, err := os.ReadFile(path)
	if err != nil {
		return out
	}
	var nf nonceFile
	if json.Unmarshal(b, &nf) != nil {
		return out
	}
	for n, exp := range nf.Used {
		if exp >= now {
			out[n] = exp
		}
	}
	return out
}

func saveNonces(path string, used map[string]float64) error {
	b, _ := json.Marshal(nonceFile{V: 1, Used: used})
	return securestore.WriteFileAtomic(path, b)
}

var reReason = regexp.MustCompile(`[^A-Za-z0-9_.:-]+`)

// readKill returns (present, reason). The reason comes from {"reason": ...} JSON or the first line of plain text,
// reduced to [A-Za-z0-9_.:-] (max 64 chars) so a hand-written KILL file can never inject text into /status.
func readKill(path string) (bool, string) {
	b, err := os.ReadFile(path)
	if err != nil {
		return false, ""
	}
	reason := ""
	var j struct {
		Reason string `json:"reason"`
	}
	if json.Unmarshal(b, &j) == nil {
		reason = j.Reason
	} else {
		reason = strings.SplitN(strings.TrimSpace(string(b)), "\n", 2)[0]
	}
	reason = reReason.ReplaceAllString(strings.TrimSpace(reason), "_")
	if len(reason) > 64 {
		reason = reason[:64]
	}
	if reason == "" || reason == "_" {
		reason = "file"
	}
	return true, reason
}

func writeKill(dir, reason string, now float64) error {
	b, _ := json.Marshal(map[string]any{"reason": reason, "at": now})
	return securestore.WriteFileAtomic(filepath.Join(dir, FileKill), b)
}
