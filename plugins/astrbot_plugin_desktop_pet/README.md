# AstrBot desktop terminal plugin

`main.py` connects a paired Coopanion Windows terminal to AstrBot's existing event pipeline. Installation and reversible integration steps are in [the deployment guide](../../docs/desktop-pet-implementation.md). This plugin requires the version-checked Companion, Memory and AstrBot patches in `overrides/desktop-pet`; it refuses to open the WSS listener when their required contracts are absent.

The default configuration is disabled. TLS uses `ca.pem`, `server.pem` and `server.key` in this plugin's private data directory. The offline `manage` module creates certificates, renews IP certificates with the same CA, pairs a device and revokes credentials. Pairing establishes the persona, bot, QQ adapter and verified user on the server. Client payloads cannot choose another identity.

Protocol v1 uses `{v,type,id,payload}` frames over authenticated WSS at `/terminal/v1`. The connection accepts `hello`, `state`, `catalog`, `event`, `result` and `ping`, and sends `welcome`, `reconcile`, `call`, `cancel`, `ack`, `error` and `pong`. Heartbeats are twenty seconds, stale peers expire after sixty-five seconds, ordinary calls time out after forty-five seconds, and frame size is limited to 2 MiB. Unknown external effects remain unknown until reconciled. A socket write alone is not an execution or display receipt.

LLM tools are `desktop_pet_capabilities`, `desktop_pet_call` and `desktop_pet_delivery`. Capability lookup exposes only the authorized live peer. Saved receipts remain queryable only in the same persona/bot/user/adapter scope. `route_proactive_message` is a cooperating-plugin API: `None` retains the caller's QQ transport, `False` defers delivery, and `True` records a submitted desktop message without duplicating it on QQ.

The Companion activity identifier is `extension:astrbot_plugin_desktop_pet:activity`. It is disabled for autonomous execution until admitted by the persona's existing activity allowlist. Planning, execution and evidence retain the native scheduler's admission, budget and checkpoint controls.
