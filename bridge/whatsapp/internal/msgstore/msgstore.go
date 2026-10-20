// Package msgstore keeps chats and messages (text + media METADATA only) in the bridge's in-memory SQLite database,
// next to whatsmeow's own tables. Tables are prefixed jarves_ so they never collide with whatsmeow_*.
package msgstore

import (
	"context"
	"database/sql"
	"strings"
)

// Media is metadata only: media is never downloaded or opened.
type Media struct {
	Mime    string `json:"mime"`
	Size    int64  `json:"size"`
	Caption string `json:"caption"`
}

// Message is the BridgeProtocol v1 message object.
type Message struct {
	ID         string  `json:"id"`
	Chat       string  `json:"chat"`
	FromMe     bool    `json:"from_me"`
	SenderName string  `json:"sender_name"`
	TS         float64 `json:"ts"`
	Kind       string  `json:"kind"`
	Text       string  `json:"text"`
	Media      *Media  `json:"media"`
	Edited     bool    `json:"edited"`
	Deleted    bool    `json:"deleted"`
}

// Chat is a stored chat row (the API adds can_send).
type Chat struct {
	ID         string  `json:"id"`
	Name       string  `json:"name"`
	Number     *string `json:"number"`
	IsGroup    bool    `json:"is_group"`
	Unread     int     `json:"unread"`
	LastTS     float64 `json:"last_ts"`
	HasHistory bool    `json:"has_history"`
	LID        *string `json:"lid"`
}

// ChatInfo updates chat metadata; empty strings keep the stored value.
type ChatInfo struct {
	ID, Name, Number, LID string
	IsGroup               bool
}

// Store wraps the database.
type Store struct{ db *sql.DB }

const schema = `
CREATE TABLE IF NOT EXISTS jarves_chats (
  id TEXT PRIMARY KEY, name TEXT NOT NULL DEFAULT '', number TEXT, lid TEXT, is_group INTEGER NOT NULL DEFAULT 0,
  unread INTEGER NOT NULL DEFAULT 0, last_ts REAL NOT NULL DEFAULT 0, has_history INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS jarves_messages (
  chat TEXT NOT NULL, id TEXT NOT NULL, from_me INTEGER NOT NULL, sender_name TEXT NOT NULL DEFAULT '',
  ts REAL NOT NULL, kind TEXT NOT NULL, text TEXT NOT NULL DEFAULT '', media_mime TEXT, media_size INTEGER,
  media_caption TEXT, has_media INTEGER NOT NULL DEFAULT 0, edited INTEGER NOT NULL DEFAULT 0,
  deleted INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (chat, id));
CREATE INDEX IF NOT EXISTS jarves_messages_ts ON jarves_messages (chat, ts);
CREATE TABLE IF NOT EXISTS jarves_meta (k TEXT PRIMARY KEY, v REAL NOT NULL);`

// SetMeta stores a numeric bridge fact (e.g. phone_last_seen).
func (s *Store) SetMeta(ctx context.Context, k string, v float64) error {
	_, err := s.db.ExecContext(ctx, `INSERT INTO jarves_meta (k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v = excluded.v`, k, v)
	return err
}

// GetMeta returns a numeric bridge fact or ok=false.
func (s *Store) GetMeta(ctx context.Context, k string) (float64, bool) {
	var v float64
	if s.db.QueryRowContext(ctx, `SELECT v FROM jarves_meta WHERE k = ?`, k).Scan(&v) != nil {
		return 0, false
	}
	return v, true
}

// RenameChat updates the name/number/lid of an EXISTING chat (contact syncs never create chats).
func (s *Store) RenameChat(ctx context.Context, c ChatInfo) error {
	_, err := s.db.ExecContext(ctx, `UPDATE jarves_chats SET name = CASE WHEN ? != '' THEN ? ELSE name END,
 number = COALESCE(?, number), lid = COALESCE(?, lid) WHERE id = ?`, c.Name, c.Name, nullable(c.Number), nullable(c.LID), c.ID)
	return err
}

// New creates the tables if needed.
func New(ctx context.Context, db *sql.DB) (*Store, error) {
	if _, err := db.ExecContext(ctx, schema); err != nil {
		return nil, err
	}
	return &Store{db: db}, nil
}

func nullable(s string) any {
	if s == "" {
		return nil
	}
	return s
}

// UpsertChat creates the chat or updates its non-empty metadata.
func (s *Store) UpsertChat(ctx context.Context, c ChatInfo) error {
	_, err := s.db.ExecContext(ctx, `INSERT INTO jarves_chats (id, name, number, lid, is_group) VALUES (?, ?, ?, ?, ?)
ON CONFLICT(id) DO UPDATE SET name = CASE WHEN excluded.name != '' THEN excluded.name ELSE name END,
 number = COALESCE(excluded.number, number), lid = COALESCE(excluded.lid, lid)`,
		c.ID, c.Name, nullable(c.Number), nullable(c.LID), c.IsGroup)
	return err
}

// AddMessage records a message: the chat gets has_history and last_ts; unread grows for a live incoming message;
// the message row itself is written only when keep is true (retention allows it and keep_nothing is off).
func (s *Store) AddMessage(ctx context.Context, m Message, keep, live bool) error {
	unread := 0
	if live && !m.FromMe {
		unread = 1
	}
	if _, err := s.db.ExecContext(ctx, `INSERT INTO jarves_chats (id, is_group, unread, last_ts, has_history)
VALUES (?, ?, ?, ?, 1) ON CONFLICT(id) DO UPDATE SET unread = unread + excluded.unread,
 last_ts = MAX(last_ts, excluded.last_ts), has_history = 1`, m.Chat, strings.HasSuffix(m.Chat, "@g.us"), unread, m.TS); err != nil {
		return err
	}
	if !keep {
		return nil
	}
	var mime, caption any
	var size any
	hasMedia := 0
	if m.Media != nil {
		mime, size, caption, hasMedia = m.Media.Mime, m.Media.Size, m.Media.Caption, 1
	}
	_, err := s.db.ExecContext(ctx, `INSERT INTO jarves_messages (chat, id, from_me, sender_name, ts, kind, text,
 media_mime, media_size, media_caption, has_media, edited, deleted) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
ON CONFLICT(chat, id) DO NOTHING`, m.Chat, m.ID, m.FromMe, m.SenderName, m.TS, m.Kind, m.Text, mime, size, caption,
		hasMedia, m.Edited, m.Deleted)
	return err
}

// EditMessage replaces the text of a stored message and marks it edited.
func (s *Store) EditMessage(ctx context.Context, chat, id, text string) error {
	_, err := s.db.ExecContext(ctx, `UPDATE jarves_messages SET text = ?, edited = 1 WHERE chat = ? AND id = ?`, text, chat, id)
	return err
}

// DeleteMessage marks a stored message deleted (revoked) and drops its text and caption.
func (s *Store) DeleteMessage(ctx context.Context, chat, id string) error {
	_, err := s.db.ExecContext(ctx, `UPDATE jarves_messages SET text = '', media_caption = CASE WHEN has_media = 1 THEN '' END,
 deleted = 1 WHERE chat = ? AND id = ?`, chat, id)
	return err
}

func scanChat(r interface{ Scan(...any) error }) (Chat, error) {
	var c Chat
	var num, lid sql.NullString
	err := r.Scan(&c.ID, &c.Name, &num, &lid, &c.IsGroup, &c.Unread, &c.LastTS, &c.HasHistory)
	if num.Valid {
		c.Number = &num.String
	}
	if lid.Valid {
		c.LID = &lid.String
	}
	return c, err
}

const chatCols = `id, name, number, lid, is_group, unread, last_ts, has_history`

// GetChat returns the chat or ok=false.
func (s *Store) GetChat(ctx context.Context, id string) (Chat, bool, error) {
	c, err := scanChat(s.db.QueryRowContext(ctx, `SELECT `+chatCols+` FROM jarves_chats WHERE id = ?`, id))
	if err == sql.ErrNoRows {
		return Chat{}, false, nil
	}
	return c, err == nil, err
}

// AllChats returns every chat, newest activity first.
func (s *Store) AllChats(ctx context.Context) ([]Chat, error) {
	rows, err := s.db.QueryContext(ctx, `SELECT `+chatCols+` FROM jarves_chats ORDER BY last_ts DESC, id`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []Chat
	for rows.Next() {
		c, err := scanChat(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, c)
	}
	return out, rows.Err()
}

// HasHistory reports whether any message was ever seen in the chat (sticky: retention purges keep the flag).
func (s *Store) HasHistory(ctx context.Context, id string) bool {
	var h bool
	if s.db.QueryRowContext(ctx, `SELECT has_history FROM jarves_chats WHERE id = ?`, id).Scan(&h) != nil {
		return false
	}
	return h
}

const msgCols = `chat, id, from_me, sender_name, ts, kind, text, media_mime, media_size, media_caption, has_media, edited, deleted`

func scanMessages(rows *sql.Rows) ([]Message, error) {
	defer rows.Close()
	out := []Message{}
	for rows.Next() {
		var m Message
		var mime, caption sql.NullString
		var size sql.NullInt64
		var hasMedia bool
		if err := rows.Scan(&m.Chat, &m.ID, &m.FromMe, &m.SenderName, &m.TS, &m.Kind, &m.Text, &mime, &size, &caption,
			&hasMedia, &m.Edited, &m.Deleted); err != nil {
			return nil, err
		}
		if hasMedia {
			m.Media = &Media{Mime: mime.String, Size: size.Int64, Caption: caption.String}
		}
		out = append(out, m)
	}
	return out, rows.Err()
}

// Messages returns the newest `limit` messages of chat with ts > since and ts >= minTS, oldest first.
func (s *Store) Messages(ctx context.Context, chat string, since, minTS float64, limit int) ([]Message, error) {
	rows, err := s.db.QueryContext(ctx, `SELECT * FROM (SELECT `+msgCols+` FROM jarves_messages
WHERE chat = ? AND ts > ? AND ts >= ? ORDER BY ts DESC, id DESC LIMIT ?) ORDER BY ts ASC, id ASC`, chat, since, minTS, limit)
	if err != nil {
		return nil, err
	}
	return scanMessages(rows)
}

// Search returns up to limit messages (newest first) whose text or media caption contains q case-insensitively
// (Unicode simple folding), restricted to chats for which readable returns true and ts >= minTS.
func (s *Store) Search(ctx context.Context, q string, readable func(string) bool, minTS float64, limit int) ([]Message, error) {
	rows, err := s.db.QueryContext(ctx, `SELECT `+msgCols+` FROM jarves_messages WHERE ts >= ? AND deleted = 0
ORDER BY ts DESC, id DESC`, minTS)
	if err != nil {
		return nil, err
	}
	all, err := scanMessages(rows)
	if err != nil {
		return nil, err
	}
	needle := strings.ToLower(q)
	out := []Message{}
	for _, m := range all {
		if len(out) >= limit {
			break
		}
		if !readable(m.Chat) {
			continue
		}
		hay := m.Text
		if m.Media != nil {
			hay += "\n" + m.Media.Caption
		}
		if strings.Contains(strings.ToLower(hay), needle) {
			out = append(out, m)
		}
	}
	return out, nil
}

// Seen sets the local unread counter of chat to 0.
func (s *Store) Seen(ctx context.Context, chat string) error {
	_, err := s.db.ExecContext(ctx, `UPDATE jarves_chats SET unread = 0 WHERE id = ?`, chat)
	return err
}

// Purge deletes stored messages with ts < before (before == nil: all). Returns the number deleted.
func (s *Store) Purge(ctx context.Context, before *float64) (int64, error) {
	var res sql.Result
	var err error
	if before == nil {
		res, err = s.db.ExecContext(ctx, `DELETE FROM jarves_messages`)
	} else {
		res, err = s.db.ExecContext(ctx, `DELETE FROM jarves_messages WHERE ts < ?`, *before)
	}
	if err != nil {
		return 0, err
	}
	return res.RowsAffected()
}

// MessageCount returns the number of stored messages per chat.
func (s *Store) MessageCount(ctx context.Context) (map[string]int, error) {
	rows, err := s.db.QueryContext(ctx, `SELECT chat, count(*) FROM jarves_messages GROUP BY chat`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := map[string]int{}
	for rows.Next() {
		var c string
		var n int
		if err := rows.Scan(&c, &n); err != nil {
			return nil, err
		}
		out[c] = n
	}
	return out, rows.Err()
}

// Wipe deletes every chat and message (unlink).
func (s *Store) Wipe(ctx context.Context) error {
	_, err := s.db.ExecContext(ctx, `DELETE FROM jarves_messages; DELETE FROM jarves_chats; DELETE FROM jarves_meta;`)
	return err
}
