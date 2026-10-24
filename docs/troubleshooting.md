# Troubleshooting

What went wrong for us building this, and why. Most are about how Darkride's scripted avatars and viewers behave.

## The bots stand stiff and bounce along, legs not moving
The .fst's joint list doesn't match the skeleton the viewer builds: a viewer ignores joint data whose count differs
from its skeleton, and applies it by index. List the joints exactly as the viewer orders them: ROOT, then the nodes
depth first, then the mesh node (docs/skeleton.md). build_bots.py does.

## The legs fold sideways, or the bots walk backwards
Joint rotations must be in the model's frame, not relative to the parent; and the viewer turns avatar models half
round, so ROOT must turn it back. The brain does both.

## Agents keep getting dropped ("Removing silent node")
- Run `duck-bot.js`, not `duck-brain.js`, as the persistent script: an agent logs and prepares the whole downloaded
  script before it connects properly, and with the big brain and several bots starting at once that takes longer
  than the domain waits.
- Old assignment clients from dropped agents can fill the pool: restart the domain and its assignment clients.
- Locally, start the assignment clients with `-a 127.0.0.1`: otherwise the domain may talk to them by the machine's
  public address, which the router may not loop back.

## Bots all take the same name
They start too close together to see each other's choices: they correct it within about 8 seconds (the higher
session id picks again). If it persists, check the message channel is the same for all (the roster's `channel`).

## A bot never notices people behind it, or never hears them
The avatar mixer only sends an agent the avatars ahead of it, plus those within its view radius all round. On
Darkride the brain sets `Agent.avatarViewRadius`; elsewhere, bots see a narrower cone.

## Bots don't answer when spoken to
Check the loudness: each bot logs "loudest nearby visitor" every 30 s; `LOUD` in the brain is the threshold (40 by
default, in mean absolute 16-bit sample units). A muted or gated microphone sends 0.

## Bots walk out onto ledges, or through railings
Railings must block at the heights the map tests (knee, mid, chest, handrail); ledges narrower than 75 cm beside a
drop are dropped from the map. If yours slip through, the railing may be a model whose collision-free mesh is just a
thin rail: add a box (an invisible one works) along it and rebuild the map.

## Textures look wrong on one bot (another bot's)
Embedded GLB textures got mixed up between models in the viewer's cache: the bots' textures are separate files named
per bot. Keep them that way.

## No display names
A scripted avatar without an account can't set a display name (the engine uses the account's): visitors see
"anonymous". Their looks tell them apart.

## A domain entity parented to a bot never shows up
The entity server can't place an entity whose parent is an avatar, so visitors never get it. The rocket flame is a
plain domain entity the bot moves itself.
