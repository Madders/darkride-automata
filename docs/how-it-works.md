# How it works

## The pieces

```
your asset server                            your domain
 bot/                                         persistent script: duck-bot.js × N
  duck-bot.js      ── loads ──►  duck-brain.js        │
  duck-cycles.json  (walks, turns, moves)              ▼
  nav.json          (the walkable map)        an assignment client per bot runs it as an
  bots.json         (the roster)              *agent*: a scripted avatar with no viewer
  duck-<id>.fst/.glb, textures                         │ avatar data, audio
  sounds/*.wav, fx-spark.png                           ▼
                                               the avatar mixer and audio mixer send it to
                                               every visitor like any other avatar
```

Each bot is a **scripted agent avatar**: the domain hands `duck-bot.js` to an assignment client, which runs it with
no window. The script sets `Agent.isAvatar = true`, wears its bot's model, and from then on moves the avatar, sets
its joints and plays sounds through it; the mixers pass all that to visitors exactly as they do for people. Nothing
runs in visitors' viewers: there is nothing for them to install.

`duck-bot.js` is a one-line loader for `duck-brain.js`. An agent writes the whole script it downloads to its log and
builds its script engine from it before it starts; with the 40 KB brain and several bots starting at once that took
long enough for the domain to drop the agents as silent. A tiny persistent script that `Script.include`s the brain
once running avoids it.

## Starting up: who am I?

Run `duck-bot.js` with as many instances as you want bots. Each copy:

1. joins the bots' message channel (`darkride.bots`, or the roster's `channel`) and listens a few seconds;
2. loads the roster, the map and the motion;
3. takes the **first bot on the roster nobody has announced** (leaving out bots marked `off`) (each bot announces itself every 8 seconds, and wears a
   model named after it, so others can tell); if two pick the same one at once, the one with the higher session id
   picks again;
4. wears that bot's model (`duck-<id>.fst`) and works out which cells of the map it can reach from where it hatches
   (its stairs rule, its rockets, its home area), so it never plans a route it can't take. A bot without a post or a
   hatching spot tries a few places at random and hatches at the one it can get furthest from: a bot that can't climb
   stairs, hatched on a raised patch with a step all round, would never leave it.

A bot run from its own loader (`bot-<id>.js`) wears its model from the moment it starts, before it has loaded
anything; without that, it shows as the engine's default avatar until it has. Copies start 6 to 15 seconds after the domain hands them out, a little apart, so most see the earlier ones' choices.

## The map

`nav.json` (from `tools/build_nav.py`) is the world cut into 25 cm cells, with every walkable surface on every level:
- **cells**: `[ix, iz, height, flags]`; flag 1 is an *edge* (a drop beside it, nothing there: something to peer over),
  flag 2 a *wall* (something solid beside it), flag 4 *covered* (something over it within 12 m: a ceiling, a roof,
  an upper floor; indoors, roughly);
- **links**: each cell's walkable neighbours (the step small enough, nothing in between at knee, mid, chest or
  handrail height, so glass and railings stop them too);
- **hops**: rocket hops, `[upper cell, lower cell]`;
- **pois**: points of interest, `{"kind", "p"}`.

Routes are A* over the links, with drops costing a lot unless they're where it's going (so it doesn't hug edges) and
walls a little.

## Deciding what to do

When it has nothing to do, a bot picks:
- **a visitor** close by for a few seconds: walk up and say hello (a chirp, a nod), now and then;
- **another bot** within 12 m: meet partway, face each other, trill and nod, every few minutes; but not a bot at
  its post (a guide or a bouncer, which would draw every bot to it) or one that already has company;
- **a move on the spot** (about one time in five): peck at the ground, sit for a while, roll (only with room ahead),
  or kick;
- **an edge**: walk to it, face the drop, lean out to peer over with an "oooh", look left and right, back away;
- **a wall**: face it and look it up and down;
- **a point of interest**: go near it and look at it;
- **somewhere**: wander.

Where it goes is mostly indoors: a covered cell always does, one with nothing over it only three times in ten
(`outdoors` in the roster), so bots go outside now and then but spend most of their time in (a map built before
2026-10-26 has no covered cells: rebuild it, and until then bots go anywhere alike). It doesn't head for
a spot that already has two bots by it. And a bot that has had two or more others within 3 m for a while (25 s, up to
75 s for a sociable one) has had enough company: it goes somewhere at least 10 m off and keeps to itself for two to
four minutes (no seeking out other bots, no walks with friends), so groups form and break up rather than grow.

Whatever it's doing, it watches the nearest visitor within 4 m with its head.

## Moving

The **walk, turn and idle cycles** are loops cut from the walk policy running in MuJoCo; the brain plays them while
it moves the avatar along its route itself, at the speed the cycle measured (sped up 1.5×), steering towards the next
cell, turning on the spot for sharp corners, its height following the floor (so it goes up and down stairs). The
**one-shot moves** (sit, roll, peck, two kicks) are played from where the bot stands and the way it faces; each frame
holds the trunk's offset and rotation from the start, so a roll travels and turns as it did in the simulator, and
the bot takes its final place and heading afterwards.

Every frame (30 a second) it sends one `Avatar.setJointRotations` with every joint's rotation in the model's frame
(see docs/skeleton.md for why), with its own head look-at blended over the cycle's head.

**Size** scales everything: a bot of size *s* has its model scaled by *s*, its steps *s* long and, as a real robot's
would be (Froude scaling), its gait √*s* times slower: walking speed goes with √*s*.

## Listening and talking

The brain reads every visitor's audio loudness (what their microphone sends). When someone within 5 m is talking, the
bot stops what it's doing, turns to face them and tilts its head this way and that; when they pause (0.65 to 1.1 s
after their last sound) it answers: a chirp, or a questioning "hm?" if they talked a while, with a nod. Its jaw, if it
has one, opens with the loudness of its own sound. Bots never speak words: they beep.

Other sounds: an alert beep when a visitor arrives, a trill greeting another bot or after a roll, "oooh" peering over
a drop, a sad note when ignored, and the rocket's whoosh. All are synthesised (`tools/make_sounds.py`), the chatter
(everything but the rockets and a bouncer's machinery) well below the level of a voice. Anything a bot says only by
chance (a chirp as it walks, an "oooh" at an edge) is chatter, and it chatters at most once in 8 s.

They're played as the bot's own avatar audio, so the audio mixer places them where the bot is and makes them quieter
with distance as it does for a person's voice: by the domain's *attenuation per doubling in distance* (Audio
settings; 0.5 by default, half as loud each time the distance doubles). Walls don't stop sound; a domain's audio
zones can (each zone pair has a coefficient), so a building's inside can be kept from hearing the bots outside.

## Personality and memory

Each bot has five traits (docs/designing-bots.md): energy, curiosity, shyness, chattiness and sociability, from the
roster or from its id. They tilt its choices: how long it idles and how briskly it walks, whether it goes to look at
things or just wanders, whether it walks up to visitors or keeps away (a shy bot backs off from a stranger who comes
within a metre or two), how much it chirps, how often it seeks out other bots. It remembers visitors (by display
name), how often it has met them and whether they've praised or scolded it, and the bots it has met; old friends get
a warmer welcome and a shy bot isn't shy with them, someone who scolded it is avoided, and bots that meet often become
friends and sometimes walk about together (one follows the other for a minute). With the bots' service running
(tools/hearing.py), each bot keeps its memories there (`GET`/`POST /memory/<id>`, saved at most once a minute and
when it stops) and gets them back when it starts.

## Voice commands

With a speech service running (the roster's `hearing`, see below) bots also understand a few spoken words. Each bot
listens to what the domain's audio mixer sends it (`Agent.isListeningToAudioStream` and `Agent.takeHeardAudio`,
Darkride's: 16-bit mono PCM at 24 kHz) and keeps the last 8 s. When a visitor pauses, the bot nearest them posts what
it heard since they started talking to `tools/hearing.py`, which recognises it offline with Vosk against a fixed
vocabulary (the command words and the bots' names: quick, and it can't mishear chatter as anything else) and answers
with the words. What was said goes out on the command channel; the bot named, or with no name any bot within earshot
of the speaker, does it:

| say | it |
|---|---|
| sit (down) | sits for 8 to 15 s |
| jump | jumps where it stands (a rocket bot fires its boots) |
| roll | rolls, if there's room |
| come (here) | comes to you |
| follow (me) | follows you for 90 s |
| stay, stop, wait | stays put for 45 s |
| go away | goes somewhere at least 10 m off, sadly |
| dance, spin | spins round |
| hello, hi | turns to you and greets you |
| good / bad, no | a happy trill / a sad note |

Not always: each bot has an `obedience` (0 to 1, default 0.7) and otherwise refuses with a sad note, a questioning
"hm?" or a kick. Bouncers ignore commands. Saying a name ("Pip, sit") picks that bot from anywhere it can hear you;
without one, every bot near you may answer.

## Guides

A bot with `"role": "guide"` (docs/designing-bots.md) runs from its own loader like a bouncer and gives tours: it
waits about its post (milling round it, within `mill` metres), walks over to offer a tour to anyone new who lingers
nearby, then leads them from stop to stop,
pausing to call them if they lag, giving up if they wander off, and saying its piece at each stop (its lines are
spoken sounds, as a bouncer's). Spoken "tour" asks for one; "no", "stop" and "go away" end it.

## Mouse bots

A bot built as a mouse bot (`"build": "mouse"`) runs the same brain with a few changes: it moves at 2.6 times the
pace (its hidden legs' walk cycle sped up with it, so it jiggles), turns four times as fast, chatters instead of
chirping, prefers walls to edges, never sits or rolls, and is always shy.

An astromech (`"build": "astromech"`) holds its neck still and turns only its dome (head_yaw) to look about, glides on
the idle cycle instead of stepping; driving, it stands as a tripod (its hips turn the side legs back about their ankle
hinges while the feet, on the ankle bones, are held level and planted; the body on neck_pitch tilts back on the
shoulders, bringing the middle leg's wheel forward), turns twice as fast, whistles, and spins its dome where another bot would sit or
roll. A power bot (`"build": "power"`) plods at about half pace on its walk cycle, honks, and never rolls.

## Rockets

Bots with `"rockets": true` (usually small ones that can't climb stairs) use the map's rocket hops as part of their
routes: they face the hop, crouch, light up (a whoosh, a particle-effect flame that follows them), lob up onto the
edge or down off it, and land in a puff of smoke. The flame is a domain entity the bot creates and moves, so the
domain must let the agent create entities (see docs/adding-to-your-world.md).

## Bouncers

A bot with `"role": "bouncer"` stands guard instead of wandering (docs/designing-bots.md): it runs from its own loader
(`bot-<id>.js` sets `BOT_ID` and includes the brain), so the wandering copies leave it alone. At its post it watches
the nearest visitor within 15 m with its hull, turns its whole body (servo whirrs, clanking feet) to face anyone more
than about 45° off, and back to its post when they've gone; challenges every visitor who comes within 12 m (once in
ten minutes each) with one of its canned sequences, or welcomes them if it recognises their name; answers anyone who
talks to it; and now and then shifts its weight or mutters to itself. Its lines are spoken sounds (make_voice.py),
queued so a sequence plays in turn.

## Commands

The message channel `<channel>.cmd` takes commands for one bot by id: `{"bot": "kip", "goto": [x, y, z]}` sends it
there (if it can reach it). Voice commands arrive there too, as `{"bot": "kip" or null, "act": "sit", "near": [x, y, z],
"speaker": <session id>}`; a script of yours can send the same to make bots sit, follow and so on.

## Settings in the brain

At the top of `duck-brain.js`: `SPEEDUP` (walk speed), `MOVE_SPEEDUP`, `TURN_RATE`, `STEER`, `LOOK_RANGE`,
`LISTEN_RANGE`, `LOUD` (the loudness that counts as talking; each bot logs the loudest visitor it heard every 30 s, to
tune it), `VIEW_RADIUS`, `FLAT_STEP` (the biggest step a bot without stairs takes) and the head's sign constants.
