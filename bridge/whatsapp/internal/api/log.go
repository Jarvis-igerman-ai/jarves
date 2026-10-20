package api

import (
	"encoding/json"
	"io"
	"os"
	"sync"
	"time"
)

// Logger writes JSON lines {ts, level, code, msg, fields} to stderr. Callers pass ONLY fixed English messages, codes
// and counts: never tokens, message text, names, numbers or JIDs (this is a rule of the bridge, tested by grep).
type Logger struct {
	mu  sync.Mutex
	out io.Writer
}

// NewLogger returns a logger writing to w (nil = stderr).
func NewLogger(w io.Writer) *Logger {
	if w == nil {
		w = os.Stderr
	}
	return &Logger{out: w}
}

// F is a field set: counts, booleans, codes and durations only.
type F map[string]any

func (l *Logger) log(level, code, msg string, f F) {
	if l == nil {
		return
	}
	if f == nil {
		f = F{}
	}
	b, err := json.Marshal(struct {
		TS     float64 `json:"ts"`
		Level  string  `json:"level"`
		Code   string  `json:"code"`
		Msg    string  `json:"msg"`
		Fields F       `json:"fields"`
	}{float64(time.Now().UnixMilli()) / 1000, level, code, msg, f})
	if err != nil {
		return
	}
	l.mu.Lock()
	defer l.mu.Unlock()
	_, _ = l.out.Write(append(b, '\n'))
}

func (l *Logger) Info(code, msg string, f F)  { l.log("info", code, msg, f) }
func (l *Logger) Warn(code, msg string, f F)  { l.log("warning", code, msg, f) }
func (l *Logger) Error(code, msg string, f F) { l.log("error", code, msg, f) }
