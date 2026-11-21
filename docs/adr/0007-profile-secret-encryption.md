# 0007 — Encrypt stored profile secrets and fleet device admin keys with Fernet and a key from `secret_key`

Date: 2026-11-21
Status: accepted

## Context

FR-fleet-builds-3 lets an operator store the secrets of a profile on the server. The project rule says that no secret goes into a database row as plain text. The server already has one high-entropy value, `secret_key`, and the session and CSRF signers in `mtfwbuilder/auth.py` use it too. A stored secret gives access to a mesh, so the batch review of #60 asked who may read, write and remove it.

## Decision

The project encrypts the secrets of a profile with Fernet from the `cryptography` package. The Fernet key is 32 bytes from HKDF-SHA256 over `secret_key`, with no salt and the info `b"mtfwbuilder profile secrets v1"`. The info value gives the profile key a value separate from the signer keys. The token goes in `profiles.secrets_ciphertext`. Only an admin session with a valid CSRF token stores, removes or replaces the stored secrets. Only an admin session gets the decrypted secrets on a load. A fleet run of an admin session builds with the stored secrets, so it also needs a valid CSRF token.

### Fleet device admin keys

The decision also covers the admin key of a fleet device (feature fleet-builds, FR 11). An admin key is the public key of the admin, not a private key. The project rule names an admin key as a secret, so the project stores it encrypted all the same. The token goes in `fleet_devices.admin_key_ciphertext`, with the same Fernet key and the same HKDF info as a profile.

The scope differs from the stored secrets of a profile in two points:

- Any session saves an admin key, because the key is public. Only an admin session gets the decrypted admin key on a load, and the fleet editor page never gets it.
- Each fleet run decrypts the admin key of each fleet device for its build, also for a session that is not an admin session.

## Consequences

- A copy of the database without `secret_key` holds no readable secret.
- A change of `secret_key` makes every stored secret unreadable. A load then names the secrets in `must_reenter` and returns a warning, and the operator enters them again.
- An operator without an admin session sees that a profile stores secrets, but gets no secret.
- A new key scheme needs a new info value, for example `v2`, and a migration of the stored tokens.
