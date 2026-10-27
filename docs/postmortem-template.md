# Postmortem: <short title>

**Date:** YYYY-MM-DD · **Authors:** · **Status:** draft / reviewed
**Blameless:** we describe what the system and process allowed, not who made a mistake.

## Summary
Two or three sentences: what broke, for how long, who was affected.

## Impact
- Duration (detected → mitigated → resolved):
- Requests failed / slow:
- Error budget used: __% of the 30-day budget

## Timeline (UTC)
| Time | Event |
|---|---|
| | Trigger (deploy, traffic spike, dependency failure) |
| | Alert fired (which one) |
| | Responder acknowledged |
| | Mitigation applied |
| | Recovered (SLIs back to normal) |

## Root cause
What actually failed and why. Use "5 whys" until you reach something you can change.

## Detection
Did the alerts fire as designed? How long from start to page? Was the runbook enough?

## What went well / what went badly / where we got lucky

## Action items
| Action | Type (prevent / detect / mitigate) | Owner | Due |
|---|---|---|---|
