Subject: How the new matching tool will get investor data

Rohit — a quick decision on how the new matching service gets investor data, and what I
need from you.

The old app that our investor records live in keeps running exactly as it does today — I'm
not touching its code. Instead, we'll set up a live copy of that database, updating
automatically a few seconds behind at most, and point the matching service at the copy.

Why not the alternatives: having the old app write directly into a second system too is
riskier than it sounds — if that second write ever fails partway, the two systems quietly
disagree about our own data, and that app holds real client data today. A fancier
live-event setup would hold up better at much bigger scale, but it's more to build and run
than we need right now for "current," not "instant," data.

What could go wrong: if the copy falls behind during a burst of activity, the matching
service works off data that's a few minutes stale, not wrong — easy to monitor. Turning
the copy on needs a short window where whoever manages our database enables the setting
that allows it; routine, but needs sign-off to schedule.

What I need from you: approval for a second, read-only database running alongside the
current one (small ongoing cost), and about 30 minutes from whoever hosts our database to
turn it on.

That's the whole decision — happy to talk it through, but nothing here needs a call.
