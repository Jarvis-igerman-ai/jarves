//go:build windows

package securestore

import (
	"errors"
	"os"
	"unsafe"

	"golang.org/x/sys/windows"
)

const (
	protection = "dpapi"
	isWindows  = true
)

func blob(b []byte) *windows.DataBlob {
	if len(b) == 0 {
		return &windows.DataBlob{}
	}
	return &windows.DataBlob{Size: uint32(len(b)), Data: &b[0]}
}

// protect wraps data with DPAPI CryptProtectData, CurrentUser scope, the given entropy, no UI.
func protect(data, entropy []byte) ([]byte, error) {
	var out windows.DataBlob
	if err := windows.CryptProtectData(blob(data), nil, blob(entropy), 0, nil, windows.CRYPTPROTECT_UI_FORBIDDEN, &out); err != nil {
		return nil, err
	}
	defer windows.LocalFree(windows.Handle(unsafe.Pointer(out.Data)))
	return append([]byte(nil), unsafe.Slice(out.Data, out.Size)...), nil
}

func unprotect(data, entropy []byte) ([]byte, error) {
	if len(data) == 0 {
		return nil, errors.New("empty")
	}
	var out windows.DataBlob
	if err := windows.CryptUnprotectData(blob(data), nil, blob(entropy), 0, nil, windows.CRYPTPROTECT_UI_FORBIDDEN, &out); err != nil {
		return nil, err
	}
	defer windows.LocalFree(windows.Handle(unsafe.Pointer(out.Data)))
	return append([]byte(nil), unsafe.Slice(out.Data, out.Size)...), nil
}

func wrapKey(key []byte) ([]byte, error) { return protect(key, []byte(Entropy)) }
func unwrapKey(w []byte) ([]byte, error) { return unprotect(w, []byte(Entropy)) }

// RestrictDir creates dir (if needed) and replaces its DACL with a protected one granting full control to the current
// user only (inherited by every file created inside), so other local users cannot read the bridge state.
func RestrictDir(dir string) error {
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return err
	}
	tok := windows.GetCurrentProcessToken()
	u, err := tok.GetTokenUser()
	if err != nil {
		return err
	}
	acl, err := windows.ACLFromEntries([]windows.EXPLICIT_ACCESS{{
		AccessPermissions: windows.GENERIC_ALL,
		AccessMode:        windows.GRANT_ACCESS,
		Inheritance:       windows.SUB_CONTAINERS_AND_OBJECTS_INHERIT,
		Trustee: windows.TRUSTEE{
			TrusteeForm:  windows.TRUSTEE_IS_SID,
			TrusteeType:  windows.TRUSTEE_IS_USER,
			TrusteeValue: windows.TrusteeValueFromSID(u.User.Sid),
		},
	}}, nil)
	if err != nil {
		return err
	}
	return windows.SetNamedSecurityInfo(dir, windows.SE_FILE_OBJECT,
		windows.DACL_SECURITY_INFORMATION|windows.PROTECTED_DACL_SECURITY_INFORMATION, nil, nil, acl, nil)
}
