//
//  duck-bot.js: the persistent script to run, one copy per bot (2026-10-24). It only loads duck-brain.js: an agent
//  writes the whole script it downloads to its log and builds its script engine from it before it starts, and with
//  the 40 KB brain that took long enough (with several bots starting at once) for the domain to drop agents as silent.
//
Script.include(Script.resolvePath("duck-brain.js"));
