# Gable — status, and what's needed from Chase

Last updated 2026-09-01 by the building agent.

## Standing decision — change nothing and watch, 2026-09-01 to 2026-09-08

Chase's call at the end of 2026-09-01: **no code changes for a week.** The
seven mechanisms below are deployed; the week is the measurement of whether
the bug rate actually dropped. What to do during it:

- **Daily:** run the thread audit against #calvo and read anything it flags.
  ```
  ssh -i ~/.ssh/gable_droplet root@143.110.146.87 \
    "cd /opt/gable && sudo -u gable ./.venv/bin/python -m tools.audit_threads --days 1"
  ```
  A flagged thread is read and classified — real input, stop, two readers,
  repeat, or something new — and written down here. Not fixed yet.
- **On any design edit:** the canary build reports in the design's thread by
  itself. Nothing to run.
- **Do not:** deploy, resume a run by hand, or edit a template to test
  something. A run that stalls is evidence; note it and leave it.

**Open after the week, in order.** A reply corpus for the conversation layer
(every real sentence typed to Gable, replayed through the model's decision
step with the expected tool frozen — the one class with no net under it); a
cancel tool, since "cancel this" currently gets an acknowledgment and no
action; scheduling the audit and the corpus refresh on the droplet, which
needs a decision on where the weekly summary posts; and Phase 2 from
CLAUDE.md §7.

**Still waiting on Chase:** confirm or reverse the silent-website credential
fallback (emptying `GABLE_DEFAULT_AGENT_CREDENTIAL` reverts it).

**Known stale or thin, noted rather than fixed during the freeze:**

- `AUDIT_2026-08-12.md`, `AUDIT_2026-08-13.md`, `ASK_CARMEN.md` and
  `CONVERSATION.md` are point-in-time documents from August and describe
  behaviour that has since changed; they are history, not instructions.
- `TEMPLATE_ISSUES.md`, `TEMPLATE_WIDTHS.md` and `TEMPLATE_CERTIFICATION.md`
  predate the canary build and the 2026-08-26 blocker kinds; the measurements
  in them are the ones the code carries, but their prose about what refuses a
  listing is older than the stop policy.
- The playground holds three test artifacts from today (`Testing_1` rows 534
  and 535, and one canary report) and the Gable drive holds their two flyers.
  Left on purpose so the runs stay auditable.


## 2026-09-01 — "How do we ensure it stops?": seven changes, all deployed, all rehearsed in the playground

Chase, after the two Under Contract threads: "This keeps happening, how do we
ensure it stops?" The honest diagnosis was three causes, not bad luck: Carmen
was the test harness, Gable was built to stop and each stop was being removed
one at a time after it hurt, and several facts had two readers that drifted.
Seven changes, each with its own commit and decision-log row:

1. **A real-address corpus.** Every address the form has ever received is
   replayed through the runner's own reader (`tools/refresh_address_corpus.py`,
   `tests/test_address_corpus.py`). Reviewing the first corpus found three more
   defects in an afternoon — "802 Dressage Ct" printed as "Dressage CT", a
   trailing slash hiding a ZIP, and 18 of 140 rows asked about for one missing
   comma the manifest already knew how to add on only one of two paths. 102 of
   140 real addresses now read as whole, up from 88.
2. **A canary build on every added or edited design.** `pipeline/canary.py`
   builds a test flyer with sample values, a sample photograph and face, runs
   the same readback, fitting and layout checks a listing gets, reports in the
   design's thread, and trashes the copy. Run live against Under Contract from
   the playground: built, found nothing wrong (Carmen had fixed the well),
   posted, copy confirmed in the Drive trash.
3. **A repeat guard.** A run never says the same sentence twice: the second
   time is one escalation naming Chase, the third is silence. Proven live on
   `Testing_1` row 534, a two-property address: ask, one re-ask with the photo
   held, escalation, then nothing — three Gable messages, run still paused.
4. **The stop policy.** A flyer that exists is sent. Only four stops survive
   after the copy, all about a fact, listed in ARCHITECTURE.md §4.7b. Proven
   live on row 535: delivered with the vision pass's note that the sample
   photo is a striped placeholder, one message, link at the end.
5. **No swallowed failures.** A repo standard walks every except handler.
6. **Rehearse before deploy.** CLAUDE.md §10 and TESTING.md §0d.
7. **A thread audit.** `tools/audit_threads.py` read the playground after the
   runs above and flagged exactly the escalated thread and passed the delivered
   one.

Also today, fixed and deployed before this build: the condo-as-two-properties
defect, the headshot audit charging the design's overhang to Gable, a built
flyer withheld over twenty points, a swallowed website timeout, and the manual
resume ignoring a stated address. Both stuck listings were delivered.

**One correction to own.** Two commits went to `main` mid-afternoon with two
docstring lint findings and without their decision rows, because a gate piped
into `tail` could not fail the chain. Fixed in the next commit; every chain
since runs with `set -o pipefail`.

**What I need from Chase:** nothing blocking. The silent-website credential
fallback from this morning remains the one judgment call to confirm.


## 2026-09-01 — two Under Contract threads: a condo called two properties, and a built flyer withheld over twenty points

Reported by Chase from #calvo with both threads pasted. Four defects, all fixed
with regression tests; deployment and the two stuck runs are covered under
"What I need from Chase".

**Lina Mariner, 10600 Partridge Ln Apt B3.** The form gave `10600 partridge
lane b3`. Gable asked for the whole address and the photo, Carmen supplied both,
and Gable then asked "which one is this post for?" — three times, while she
said three ways that it was one condo. The house number is five digits, and the
two-property check counted it as a second ZIP. The same pattern had already
made the opening message say only "it has no state" when the address had
neither a state nor a ZIP. `listings.address.zip_codes` is now the one ZIP
reader and leaves the house number out; the manifest, the incomplete-address
sentence and the research identity window use it.

**Brittney Bushee, 2038 Kurtz Ave.** Three things, in sequence.

1. Gable built the flyer, then said the agent photo ran 20 points past the
   bottom edge and did not send the link. Measured through the service
   account: the design's own headshot well bled past the page, and the face was
   created inside it, clipped clear of the title band — 40 points lower and 20
   points shorter than the well — so the audit matched it to no frame and
   charged the design's overhang to Gable. A created image inside a frame Gable
   deleted now inherits that frame's overhang.
2. Carmen said "That's ok. Please send it and I can adjust" and was refused.
   The flyer existed; its link was in the database. A layout regression is now
   delivered with the measurement under the link, the way a vision finding has
   been since 2026-08-17. Chase's rule: Gable produces the flyer no matter what.
3. "Recheck it" rebuilt from the source Carmen had just edited, and the
   official-site profile read timed out once. The exception was swallowed with
   no log line, the memoised gate reused the failure for the credential phase,
   and the pause told her to correct the request or the roster — both right.
   The read is retried once after a transient failure and logs the cause; a
   silent site on a complete roster row now yields the brokerage credential and
   says so in the delivery message; a pause the silence still forces names the
   true remedy, which is to run again.

**Verified.** The whole suite, `mypy --strict` and `ruff` pass with the new
tests: the condo address, the clipped face inside a bleeding well, the
delivered layout note, the retry, and every silent-site branch.
`agents/profile_lookup.py` was split from `website.py` at the 800-line ceiling.

**Deployed (`1207109`) and the runs resumed from the droplet.** Brittney's
flyer is in her thread, built from the source Carmen edited at 20:01, with no
layout note and the credential read from her profile — the site answered this
time. Lina's first resume found a fifth defect: `tools/run_row.py --resume`
re-read the sheet's `10600 partridge lane b3` instead of the address Carmen
had stated in the thread, and posted one more address ask there. The Slack
path had always laid the stated address over the row; the tool did not.
`store.stated_address` is now the one reader both use, with a test. That
message in her thread is this session's, not Gable's design.

**Not a defect, but parked:** a Sold listing from 2026-08-31 evening is still
waiting for its property photo in its own thread.

## What I need from Chase

- **One judgment call to confirm.** A silent website now yields the brokerage
  credential when the roster row is complete. Emptying
  `GABLE_DEFAULT_AGENT_CREDENTIAL` restores the old stop, as the 2026-08-19
  decision promised; nothing else changes.
- **If Lina's thread still shows no flyer**, the second resume did not land.
  Reply "run it again" in her thread, or from the droplet:

  ```
  ssh -i ~/.ssh/gable_droplet root@143.110.146.87 \
    "cd /opt/gable && sudo -u gable ./.venv/bin/python -m tools.run_row 'Form Responses 1' 136 --resume"
  ```

Older entries: `STATUS_ARCHIVE_2026-08-27.md`, then the archives before it.
