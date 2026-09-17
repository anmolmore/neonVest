Subject: How the new matching tool gets investor data

Rohit — decision on how the matching service gets investor data, plus what I need from you.

Old app keeps running untouched — no code changes there. We stand up a live copy of that
database, a few seconds behind at most, and point the matching service at the copy.

Why not the alternatives: having the old app write into a second system too is riskier —
if that second write ever fails partway, the two systems quietly disagree, and that app
holds real client data today. A fancier live-event setup scales better long-term, but is
more to build than we need for "current," not "instant," data.

What could go wrong: copy falls behind during a traffic burst — service works off data a
few minutes stale, not wrong. Easy to monitor. Turning the copy on needs a short window
where whoever manages the database enables it — routine, needs sign-off to schedule.

What I need from you: approval for a second, read-only database (small ongoing cost), and
~30 minutes from whoever hosts our database to turn it on.

That's the whole decision. Happy to talk it through, but nothing here needs a call.
