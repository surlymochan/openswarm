# Antigravity macOS runtime

`bin/agy-via-local-proxy` launches the existing Antigravity CLI with the configured local proxy. Its OAuth credential is stored as base64-encoded JSON in the login Keychain, under account `openswarm` and service `openbot-agy-nick-oauth`. The wrapper restores it to a private home on a RAM disk and recreates that home after a restart. Configure the Keychain item through an authenticated GUI session; never print its contents.

The wrapper leaves the existing user's Antigravity profile untouched. It saves the credential back only when the refresh token changes. An unavailable or locked login Keychain, or a failed RAM disk mount, stops the launch rather than placing credentials on disk. A full machine restart still requires a logged-in user session and an unlocked login Keychain. The wrapper must run as the same logged-in user, with `OPENSWARM_HOME` set by the executor service.
