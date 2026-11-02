"""Bounded desktop context; appearance does not create another character."""
EMBODIMENT = (
    "The desktop figure is YOUR OWN embodied form, an additional body of the same persona speaking on QQ. "
    "Its skin changes your appearance, never your identity. A poke or pat touches YOU through this body; "
    "do not describe it as a separate pet you own or control. Use first-person embodied language. "
    "Keep desktop touch distinct from QQ poke. Ordinary touch needs a brief natural response in your persona; "
    "do not search or call unrelated tools just to acknowledge it."
)


def snapshot_for(peer, observations, *, light=False):
    return {"state": peer.state, "recent_observations": observations[-(3 if light else 8):],
            "worlds": [{"id": w["id"], "supported": w.get("supported"),
                        "environment": str(w.get("environment", ""))[:4000] if w["id"] == "desktop-pet" and not light else "",
                        "tool_count": len(w.get("tools", [])),
                        "tools": [{"name": t["name"], **({"description": t["description"][:160]} if not light else {})}
                                  for t in w.get("tools", [])[:12]]} for w in peer.catalog["worlds"]]}
