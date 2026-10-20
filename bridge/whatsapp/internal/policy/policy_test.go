package policy

import (
	"encoding/hex"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

type table struct {
	KeyHex   string                     `json:"key_hex"`
	Now      float64                    `json:"now"`
	Defaults map[string]json.RawMessage `json:"defaults"`
	Rows     []map[string]json.RawMessage
}

type tState struct {
	Mode       string   `json:"mode"`
	Killed     bool     `json:"killed"`
	LocalHour  int      `json:"local_hour"`
	SendAllow  []string `json:"send_allow"`
	History    []string `json:"history"`
	Sent       [][]any  `json:"sent"`
	UsedNonces []string `json:"used_nonces"`
}

type tApproval struct {
	Kind  string  `json:"kind"`
	Night bool    `json:"night"`
	ExpIn float64 `json:"exp_in"`
}

const tableNonce = "00000000000000000000000000000001"

// merge applies the row object's keys over the defaults object (shallow), recursing into "approval".
func merge(def, over json.RawMessage) json.RawMessage {
	var a, b map[string]json.RawMessage
	_ = json.Unmarshal(def, &a)
	if over != nil {
		_ = json.Unmarshal(over, &b)
	}
	for k, v := range b {
		if k == "approval" && a[k] != nil {
			v = merge(a[k], v)
		}
		a[k] = v
	}
	out, _ := json.Marshal(a)
	return out
}

func textOf(raw json.RawMessage) string {
	var s string
	if json.Unmarshal(raw, &s) == nil {
		return s
	}
	var r struct {
		Repeat string `json:"repeat"`
		N      int    `json:"n"`
	}
	_ = json.Unmarshal(raw, &r)
	return strings.Repeat(r.Repeat, r.N)
}

func loadTable(t *testing.T) table {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join("..", "..", "testdata", "policy_table.json"))
	if err != nil {
		t.Fatalf("policy table: %v", err)
	}
	var tb table
	if err := json.Unmarshal(raw, &tb); err != nil {
		t.Fatalf("policy table json: %v", err)
	}
	return tb
}

func TestSharedPolicyTable(t *testing.T) {
	tb := loadTable(t)
	key, err := hex.DecodeString(tb.KeyHex)
	if err != nil || len(key) != 32 {
		t.Fatalf("key_hex")
	}
	if len(tb.Rows) < 30 {
		t.Fatalf("table too small: %d rows", len(tb.Rows))
	}
	for _, row := range tb.Rows {
		var name string
		_ = json.Unmarshal(row["name"], &name)
		t.Run(name, func(t *testing.T) {
			var st tState
			if err := json.Unmarshal(merge(tb.Defaults["state"], row["state"]), &st); err != nil {
				t.Fatal(err)
			}
			var rq map[string]json.RawMessage
			_ = json.Unmarshal(merge(tb.Defaults["request"], row["request"]), &rq)
			var to string
			_ = json.Unmarshal(rq["to"], &to)
			text := textOf(rq["text"])
			var ap tApproval
			_ = json.Unmarshal(rq["approval"], &ap)

			p := Payload{V: 1, N: tableNonce, To: to, H: TextHash(to, text), Exp: tb.Now + ap.ExpIn, Night: ap.Night}
			id := ""
			switch ap.Kind {
			case "valid", "expired", "far_future":
				id = Sign(key, p)
			case "none":
			case "garbage":
				id = "wa1.this-is-not.an-approval"
			case "bad_sig":
				id = Sign([]byte("another key, not the bridge one"), p)
			case "wrong_to":
				p.To = "972549999999@s.whatsapp.net"
				p.H = TextHash(p.To, text)
				id = Sign(key, p)
			case "wrong_text":
				p.H = TextHash(to, text+" (changed)")
				id = Sign(key, p)
			case "reused":
				id = Sign(key, p)
				st.UsedNonces = append(st.UsedNonces, tableNonce)
			default:
				t.Fatalf("unknown approval kind %q", ap.Kind)
			}
			allow := map[string]bool{}
			for _, j := range st.SendAllow {
				allow[j] = true
			}
			hist := map[string]bool{}
			for _, j := range st.History {
				hist[j] = true
			}
			used := map[string]bool{}
			for _, n := range st.UsedNonces {
				used[n] = true
			}
			var sent []Sent
			for _, s := range st.Sent {
				sent = append(sent, Sent{TS: tb.Now - s[0].(float64), To: s[1].(string)})
			}
			d := Check(tb.Now, key, State{
				Killed: st.Killed, Mode: Mode(st.Mode), LocalHour: st.LocalHour, SendAllow: allow,
				HasHistory: func(j string) bool { return hist[j] }, Sent: sent,
				UsedNonce: func(n string) bool { return used[n] }, Connected: true,
			}, Request{To: to, Text: text, ApprovalID: id})

			var expOK string
			if json.Unmarshal(row["expect"], &expOK) == nil {
				if expOK != "OK" || !d.OK {
					t.Fatalf("want OK, got %s %s", d.Code, d.Limit)
				}
				if d.Approval == nil || d.Approval.N != tableNonce {
					t.Fatalf("OK decision must carry the approval to consume")
				}
				return
			}
			var exp struct {
				Code  string `json:"code"`
				Limit string `json:"limit"`
			}
			_ = json.Unmarshal(row["expect"], &exp)
			if d.OK || d.Code != exp.Code || d.Limit != exp.Limit {
				t.Fatalf("want %s/%s, got ok=%v %s/%s", exp.Code, exp.Limit, d.OK, d.Code, d.Limit)
			}
			if d.Code == "WA_RATE_LIMITED" && d.RetryAfterS < 1 {
				t.Fatalf("retry_after_s must be >= 1")
			}
		})
	}
}

func baseState() State {
	return State{Mode: ModeSend, LocalHour: 12, SendAllow: map[string]bool{"972541112222@s.whatsapp.net": true},
		HasHistory: func(string) bool { return true }, Connected: true}
}

var testKey = []byte("0123456789abcdef0123456789abcdef")

const now = 1790000000.0

func validID(to, text string, exp float64) string {
	return Sign(testKey, Payload{V: 1, N: tableNonce, To: to, H: TextHash(to, text), Exp: exp})
}

func TestApprovalEdgeCases(t *testing.T) {
	to, text := "972541112222@s.whatsapp.net", "hello"
	good := validID(to, text, now+60)
	parts := strings.Split(good, ".")
	cases := map[string]struct {
		id   string
		code string
	}{
		"exactly_300_ahead_ok": {validID(to, text, now+300), ""},
		"301_ahead_invalid":    {validID(to, text, now+301), "WA_APPROVAL_INVALID"},
		"exp_equals_now_ok":    {validID(to, text, now), ""},
		"exp_just_passed":      {validID(to, text, now-0.5), "WA_APPROVAL_EXPIRED"},
		"wrong_prefix":         {"wa2." + parts[1] + "." + parts[2], "WA_APPROVAL_INVALID"},
		"padded_sig":           {good + "=", "WA_APPROVAL_INVALID"},
		"extra_part":           {good + ".x", "WA_APPROVAL_INVALID"},
		"empty_sig":            {"wa1." + parts[1] + ".", "WA_APPROVAL_INVALID"},
		"truncated_sig":        {"wa1." + parts[1] + "." + parts[2][:20], "WA_APPROVAL_INVALID"},
		"sig_over_other_bytes": {"wa1." + parts[1] + "A." + parts[2], "WA_APPROVAL_INVALID"},
		"text_trailing_space":  {validID(to, text+" ", now+60), "WA_APPROVAL_MISMATCH"},
	}
	for name, c := range cases {
		t.Run(name, func(t *testing.T) {
			d := Check(now, testKey, baseState(), Request{To: to, Text: text, ApprovalID: c.id})
			if c.code == "" && !d.OK {
				t.Fatalf("want OK got %s", d.Code)
			}
			if c.code != "" && d.Code != c.code {
				t.Fatalf("want %s got %s (ok=%v)", c.code, d.Code, d.OK)
			}
		})
	}
}

func signRaw(key []byte, raw string) string {
	body := ApprovalPrefix + b64.EncodeToString([]byte(raw))
	return body + "." + b64.EncodeToString(mac(key, body))
}

func TestApprovalPayloadShape(t *testing.T) {
	to, text := "972541112222@s.whatsapp.net", "hi"
	h := TextHash(to, text)
	bad := []string{
		`not json`,
		`{"v":2,"n":"` + tableNonce + `","to":"` + to + `","h":"` + h + `","exp":1790000060,"night":false}`,
		`{"v":1,"n":"short","to":"` + to + `","h":"` + h + `","exp":1790000060,"night":false}`,
		`{"v":1,"n":"` + strings.ToUpper("0000000000000000000000000000000a") + `","to":"` + to + `","h":"` + h + `","exp":1790000060}`,
		`{"v":1,"n":"` + tableNonce + `","to":"` + to + `","h":"xyz","exp":1790000060}`,
		`{"v":1,"n":"` + tableNonce + `","to":"` + to + `","h":"` + h + `","exp":"1790000060"}`,
		`{"v":1,"n":"` + tableNonce + `","to":"` + to + `","h":"` + h + `","exp":1790000060,"night":"yes"}`,
		`{"v":1,"n":"` + tableNonce + `","h":"` + h + `","exp":1790000060}`,
		`[1,2]`,
	}
	for i, raw := range bad {
		d := Check(now, testKey, baseState(), Request{To: to, Text: text, ApprovalID: signRaw(testKey, raw)})
		if d.Code != "WA_APPROVAL_INVALID" {
			t.Fatalf("case %d: want INVALID got %s", i, d.Code)
		}
	}
	// float exp and missing night are accepted
	ok := `{"v":1,"n":"` + tableNonce + `","to":"` + to + `","h":"` + h + `","exp":1790000060.5}`
	if d := Check(now, testKey, baseState(), Request{To: to, Text: text, ApprovalID: signRaw(testKey, ok)}); !d.OK {
		t.Fatalf("float exp: %s", d.Code)
	}
}

func TestRecipientShapesAndNotConnected(t *testing.T) {
	st := baseState()
	for _, to := range []string{"972541112222:3@s.whatsapp.net", "+972541112222", "abc@s.whatsapp.net", "status@broadcast", ""} {
		if d := Check(now, testKey, st, Request{To: to, Text: "x", ApprovalID: "x"}); d.Code != "WA_BAD_RECIPIENT" {
			t.Fatalf("%q: %s", to, d.Code)
		}
	}
	st.Connected = false
	to := "972541112222@s.whatsapp.net"
	if d := Check(now, testKey, st, Request{To: to, Text: "x", ApprovalID: validID(to, "x", now+10)}); d.Code != "WA_NOT_CONNECTED" || d.HTTP != 503 {
		t.Fatalf("not connected: %s", d.Code)
	}
}

func TestRateRetryAfterAndCounts(t *testing.T) {
	sent := []Sent{{now - 10, "a"}}
	if l, r := RateCheck(now, sent, "a"); l != "per_15s" || r != 5 {
		t.Fatalf("%s %d", l, r)
	}
	var hour []Sent
	for i := 0; i < 10; i++ {
		hour = append(hour, Sent{now - 3000 + float64(i*100), "a"})
	}
	if l, r := RateCheck(now, hour, "a"); l != "per_hour" || r != 600 {
		t.Fatalf("%s %d", l, r)
	}
	rec := []Sent{{now - 900, "b"}, {now - 600, "c"}, {now - 300, "d"}, {now - 200, "b"}}
	if l, r := RateCheck(now, rec, "a"); l != "recipients_per_hour" || r != 3000 {
		t.Fatalf("%s %d", l, r)
	}
	a, h, d, rh := RateCounts(now, append(rec, Sent{now - 5, "a"}, Sent{now - 50000, "z"}))
	if a != 1 || h != 5 || d != 6 || rh != 4 {
		t.Fatalf("%d %d %d %d", a, h, d, rh)
	}
	if !QuietHour(23) || !QuietHour(0) || !QuietHour(6) || QuietHour(7) || QuietHour(22) {
		t.Fatal("quiet hours")
	}
}
