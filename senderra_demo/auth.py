"""One shared password, checked before anything else renders.

WHY THIS EXISTS AT ALL
----------------------
On Streamlit Community Cloud the app's URL is reachable by anyone who has it.
This app's Upload tab writes to `docs-in/`, and that write is what starts the
pipeline — so an ungated deployment is a stranger's button for spending the
Azure budget, not merely a data leak. The gate is therefore in front of the
whole app, including the read-only dashboard, because the upload tab cannot be
protected separately without also protecting the session it lives in.

WHAT THIS IS NOT
----------------
It is not authentication. There are no accounts, no per-user identity and no
audit trail — one secret, shared by everyone who is meant to see the demo, which
is exactly the trust model of "here is the link and the password". If the demo
ever needs to know *who* looked, that is Streamlit's viewer allowlist or an
identity provider in front, not this file.

Two details that are not decoration:

* `hmac.compare_digest`, not `==`. String equality returns as soon as two bytes
  differ, which leaks the length of the shared prefix through response timing.
  The comparison is cheap; being careless here is a habit that costs nothing to
  avoid and would matter on the day this code is copied somewhere it counts.
* The submitted password is dropped from `st.session_state` once checked, so it
  does not sit in the session store for the lifetime of the tab.
"""
from __future__ import annotations

import hmac

import streamlit as st

_UNLOCKED = "_auth_ok"
_FIELD = "_auth_password"
_FAILED = "_auth_failed"


def _check(expected: str) -> None:
    supplied = st.session_state.pop(_FIELD, "")
    if not supplied:
        return
    if hmac.compare_digest(supplied.encode(), expected.encode()):
        st.session_state[_UNLOCKED] = True
        st.session_state.pop(_FAILED, None)
    else:
        st.session_state[_FAILED] = True


def gate(password: str) -> bool:
    """True when this session may see the app.

    Returns rather than calling `st.stop()` so the caller decides what an
    unlocked session means — `app.py` stops, but a test or a future read-only
    embed can branch on it.
    """
    if not password:
        # Nothing to check against. `Settings.gate_required` is what makes this
        # state impossible on a public host; locally it is the normal case.
        return True

    if st.session_state.get(_UNLOCKED):
        return True

    _check(password)
    if st.session_state.get(_UNLOCKED):
        return True

    st.markdown("### Senderra IDP")
    st.caption("Two-stage split extraction over Azure Content Understanding and "
               "Azure OpenAI. This demo reads live pipeline data.")
    st.text_input("Password", type="password", key=_FIELD,
                  on_change=_check, args=(password,),
                  help="Shared password for this demo. Ask whoever sent you the "
                       "link.")
    if st.session_state.get(_FAILED):
        st.error("Incorrect password.")
    st.caption("Entering the password loads live data from Azure Blob Storage, "
               "which takes a few seconds on the first screen.")
    return False
