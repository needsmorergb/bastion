"""Typed error hierarchy for ``bastion.funder`` (SESS-02, SESS-03, SEC-02).

Every raised instance carries only a plain message string — never include
the vault secret, the vault ``Keypair``, or a signed transaction blob in
any error string raised from this module or its subclasses. Amounts (SOL/
lamports) are non-secret and may appear in messages.
"""


class FunderError(Exception):
    """Base class for all funder failures."""


class FunderCapExceededError(FunderError):
    """Raised when the requested amount strictly exceeds
    ``config.max_session_cap_sol`` (D-03). Refuse-before-send: raised
    before any RPC call touches the network."""


class FunderInsufficientBalanceError(FunderError):
    """Raised when the vault balance cannot cover the requested amount plus
    the exact network fee (D-04). Refuse-before-send: raised before signing
    or sending anything."""


class FunderInvalidAmountError(FunderError):
    """Raised when ``amount_sol`` is non-positive/non-finite, or
    ``session_pubkey`` is not a valid base58 pubkey (V5 input validation),
    before any lamport conversion or network call."""
