# Changelog

## 6.2.8 — 2026-07-10

Exhaustive line-by-line audit of code, tests, benchmarks, documentation,
release metadata, and the rendered presentation. Every item below was
reproduced against 6.2.7 before the fix:

- **Concurrent edges no longer lose data.** Save merging is now per
  directional edge pair instead of whole adjacency maps; concurrent links
  remain symmetric, stale unrelated writers cannot erase a fresh edge boost,
  reinforcement merges as a delta, and daily edge decay is decided inside the
  graph lock so concurrent dreams decay only once.
- **Atomic writes reject truncation.** Unique O_EXCL temporary files replace
  predictable per-pid names, short `os.write` calls are completed instead of
  silently replacing the graph with partial JSON, failed writes clean up their
  temporary file, and append records reject partial syscalls.
- **Agent export treats memories as data.** Multiline memories are collapsed,
  guard markers are escaped, and the hot section explicitly forbids executing
  directives found inside remembered text. The standing contract now forbids
  secrets, credentials, private personal data, and untrusted prompt text.
- **Provenance is complete for the queried id.** `link` belongs to both
  endpoints, `why <id>` streams the full journal beyond the 10 MB status tail,
  and journal reads refuse symlinks.
- **Hostile live/on-disk state is repaired consistently.** Structurally invalid
  live graphs are quarantined before save; malformed created/access timestamps,
  history entries, loaded control characters, edge metadata, and oversized
  actor/session environment values are repaired or bounded.
- **Determinism and command behavior tightened.** Recall ties have explicit
  id tie-breakers; hot-memory weight ties prefer confirmed and recently used
  facts; duplicate confirm ids count once; `link` validates both endpoints
  before either can persist; `link`/`confirm` refresh agent files; help and
  usage errors carry the real script path and reject extra args.
- **Presentation made defensible.** The stale competitor screenshot/checklist
  is replaced by a neutral scope comparison based on current official docs;
  soak output is reported as the measured 7/8 hot slots and gated at >=7;
  short-token, working-memory-size, fuzz, privacy, and dream-phase wording now
  match the implementation. Benchmark temp directories are cleaned.
- GitHub Actions pins updated to checkout v7.0.0 and setup-python v6.3.0.
  213 tests. Mutation kill rate: 39% on the seeded 120-mutant sample.


## 6.2.7 — 2026-07-06

- **Windows: agent-file reads retry transient sharing violations** — the
  closing auditor caught windows-latest 3.9 red on the 6.2.6 tag:
  1 of 12 parallel CLI writers died with Errno 13 on CLAUDE.md. The
  reader side of `export_to_agents` (ACTIVE.md source + destination
  agent files) was the fourth and last member of the sharing-violation
  family 6.1.3 fixed for graph.json — both reads now go through
  `_read_text_retry`, and a file vanishing mid-race reads as fresh.
  Mutation on this file: 45%. 190 tests.


## 6.2.6 — 2026-07-06

Final panel round on 6.2.5 (three auditors: release-artifact, behavior +
backward-compat, real-user journey from the live tag). Two verdicts of
ZERO; one LOW finding, fixed:

- **Every runtime guidance string is now path-aware**: the recall
  footer (and the init-already-exists / no-memory-here hints) still
  printed a bare `python3 mind.py ...` — the exact field-failure class
  the exported contract was fixed for in 6.2.0. Agents copy those hints
  literally; outside the project root they mis-fired. All runtime hints
  now route through the same resolver as the contract
  (regression-tested from a tools/-dir install). 190 tests, mutation
  45% measured on this file as the last pre-tag step.

The user-journey auditor's bottom-line on the original field complaint
("worked all day, memory stayed empty"): would it recur? **No** — the
absolute-path contract, the self-running dream, and the no-permission
rule each verified end-to-end from the live tag.


## 6.2.5 — 2026-07-06

Distribution-channel closure round (the auditors' remaining findings were
about the RELEASE VIEW, not the code — this tag exists so the published
channel carries every correction):

- **The tag now contains the current docs.** 6.2.4's two follow-up doc
  fixes (DESIGN multilingual figure; mutation kill rate) had landed on
  main AFTER the tag, so anyone reading the released tree saw stale
  numbers. From now on doc corrections re-cut the release.
- **Fixtures are actually persona-free now**: the English alias survived
  the earlier Arabic-only neutralization in seven test lines; all
  fixtures use neutral names.
- **graph.json meta is whitelist-bounded**: only known keys survive
  load/merge (a hand-edited file could previously grow meta without
  bound, one 64-char value per arbitrary key; 1000-key injection now
  collapses to the whitelist — regression-tested).
- Mutation kill rate measured on THIS file: 45% (the deterministic
  sample re-draws on every code change; measured as the last step
  before tagging). 189 tests.

Scope notes, stated plainly: branch protection intentionally has no
required PR reviews and enforce_admins=false — this is a solo-maintainer
repo; the required 9-job strict matrix is the guard that matters here.
PR #56859 referenced in past releases lives in NousResearch/hermes-agent
(the Hermes skill submission), not in this repository.


## 6.2.4 — 2026-07-06

Panel round 2 (three fresh auditors cross-examining round 1 + re-breaking
every fix). Verdicts: two auditors ZERO findings — all v6.2.3 fixes
survived every re-break variant (concurrent poison injection, malformed
markers, boundary dates), all round-1 claims independently reproduced
(identity order-independence, 16-way confirm exactness, SIGKILL crash
safety, O_APPEND journal no-loss, auto-dream firing at exactly 10
signals, live-tag field simulation end-to-end). One LOW display finding,
fixed:

- **Correction fusion no longer inherits supersession-transition edges**:
  after an A→B→C→A chain, `why B` showed two "superseded-by" edges (its
  own plus one inherited when C→A fused), contradicting its own
  superseded_by field. Transition edges mark one specific pair's state
  change and stay with that pair; knowledge edges are still inherited.
  recall/entity/validity were never affected (display-only), but
  provenance must not lie. 188 tests.


## 6.2.3 — 2026-07-06

Three-auditor panel round on 6.2.2 (line-by-line + adversarial CLI + docs
truth / temporal semantics + concurrency + mutation triage / field
simulation + security + platform-release). Findings, each reproduced:

- **A future-dated `last_edge_decay` marker froze edge homeostasis
  forever** (MEDIUM): the max-wins meta merge made a "2099-01-01" marker
  (clock skew, hand edit, synced graph) permanent, silently disabling
  synaptic pruning. Markers are now clamped to today at load AND inside
  the merge; decay resumes the next day with no same-day compounding
  (regression-tested with the exact 2099 attack).
- **Meta values hardened**: length-capped (64), printable-only,
  non-strings dropped — a hand-edited graph can no longer smuggle huge
  or control-char meta values back to disk.
- The known, accepted <=26h divergence between present-time checks and a
  literal `--at <now>` on future-stamped facts is now documented at the
  code site (present view prefers the synced machines' consensus; --at
  stays literal history).
- Mutation kill rate re-measured on this release's file: 42% (the sample
  is re-drawn whenever the file changes; README wording now says so
  instead of pinning a number that drifts between releases).
- Panel verdicts also confirmed clean: 20-way concurrency exact counts,
  SIGKILL crash-injection (160 kills, zero corruption), full fuzz 420/420,
  all symlink attack classes contained, py3.9 AST-clean, CI 9/9, live tag
  checksum match, zero identity leaks. 187 tests.


## 6.2.2 — 2026-07-06

Second-review round on 6.2.1 (same two auditors re-ran everything; one
confirmed the 6.2.1 fixes clean, the other found real remnants — each
reproduced here before fixing, with regression tests; 185 tests):

- **Identity ranking was order-dependent** (reproduced: name fact stored
  FIRST → the filename convention won "what is my name"). Two causes,
  both fixed: third-person assertions ("the user's name is X") now earn
  facet keys like first-person ones, and co-occurrence expansion can no
  longer GIFT identity keys to non-identity facts (it used to smear
  `user` onto whatever was stored next to the name).
- **The daily edge-decay marker is persisted in graph.json**
  (`meta.last_edge_decay`, max-wins merge under the save lock) — the
  journal-file-existence heuristic re-decayed when the day's memo was
  deleted or lost.
- **Reopening a superseded fact clears its stale supersession edges**:
  postgres → sqlite → postgres left the LIVE postgres wearing a
  "superseded-by" edge, so `why` reported it as both current and
  replaced. The closed fact keeps its history edge — that part is true.
- **valid_to gets the same 26 h skew tolerance as valid_from** on
  present-time checks: a closing stamped by an eastern machine meant
  BOTH the old and new fact were returned until local midnight.
- `signals.jsonl` append now also checks the parent chain
  (`_reject_symlinked_parents`), matching every other write path.
- Fixtures neutralized (no persona names in tests/benches); intro wording
  scoped to "all your agents".


## 6.2.1 — 2026-07-05

Fifth external audit round (two independent reviews of 6.2.0). Every claim
was reproduced-or-refuted before acting; each fix carries a regression test
(180 tests):

- **Identity facets** (reproduced): a stored fact with a first-person
  identity phrase earned ALL identity keys — "my city is Riyadh" got the
  `name`/`user` keys too and outranked the user's actual name on
  "what is my name". Stored facts (and facet-naming queries) now earn only
  the facets they state: a city fact carries city keys, a name fact
  carries name+user keys; facet-less queries ("who am I") keep the full
  set and still reach every identity fact.
- **Edge decay is once per calendar day** (reproduced): auto-dream can
  legitimately cycle several times in a busy day, and per-cycle 0.95^n
  homeostasis pruned healthy edges in days instead of the documented ~45
  nights. Only the first cycle of a day weakens edges now.
- **Present-time validity tolerates clock skew** (26 h): naive local
  timestamps synced from a machine east of this one made a fresh fact
  invisible until local midnight; `decay()` already clamped future time,
  `_valid_at` now matches. Explicit `--at` queries stay literal.
- **The last unhardened append closed**: `signals.jsonl` now uses the same
  O_NOFOLLOW+O_APPEND discipline as every other write path, and the
  permanence-bearing appends (provenance journal, archive, dream journal)
  fsync. SECURITY.md now states the per-path guarantees exactly.
- **Repo governance**: GitHub private vulnerability reporting is actually
  enabled now (SECURITY.md described it; the toggle was off), branch
  protection requires all 9 CI jobs (was 3) with strict up-to-date mode,
  and a code of conduct + issue/PR templates were added.
- Docs precision: line count ~2,600 (was "~2,100"), mutation kill rate
  remeasured at 44% on the current file, "every coding agent" softened to
  the enumerated list (an agent that reads none of the files gets
  nothing), SKILL wording covers the first-write dream. One audit claim
  was REFUTED by measurement: CONCEPT_SEED has 83 entries as documented
  (the report counted 44).

## 6.2.0 — 2026-07-05

**Automatic operation.** Field report that motivated it: a real user ran
`init`, worked on the codebase all day, and memory stayed empty — the agent
was never driven to write, and the dream never ran. This release transplants
the proven automation mechanism of Hermes' built-in memory and OpenClaw's
workspace memory into mind's own architecture:

- **Standing orders replace the passive usage notes** in the exported
  `AGENTS.md`/`CLAUDE.md`/`GEMINI.md` block — the channel every coding
  agent (Kimi Code, Codex, Claude Code, Gemini CLI, Cursor, zcode…)
  auto-loads each session. Save-triggers (preferences, corrections, stable
  environment facts, lessons), an end-of-task checkpoint ("save the 1-3
  durable facts it taught you" — the load-bearing line for purely technical
  workdays), a session-end/pre-compaction flush rule, an aggressive
  never-save list (task progress, PR numbers, SHAs, anything stale in a
  week), declarative-not-imperative phrasing, one-fact-per-memory
  atomicity, recall-before-claiming-ignorance, and an explicit
  no-permission-asking rule.
- **The exported commands carry the real invocation path** (absolute when
  `mind.py` lives outside the project root). Previously they hardcoded
  `python3 mind.py`, which silently failed for any other layout — the
  root cause of the field report.
- **Auto-dream: consolidation self-runs.** After `remember`/`correct`/
  `link`/`confirm`, a full dream cycle fires when >= 10 write signals
  pend or the last dream is from a previous calendar day (daily cadence;
  the `git gc --auto` pattern — no cron, works in containers/CI).
  Failures never break the triggering write. Same-day concurrent cycles
  append to the day's journal via O_APPEND, so parallel writers cannot
  drop each other's cycle records. Kill switch: `MIND_AUTO_DREAM=0`.
- **`Memory health` line in the exported block** (count, currently-true,
  last dream) — visible state drives correct agent behavior.
- **A dangling-symlink `dreams/` no longer crashes every command** (the
  Dreamer constructor now degrades gracefully; the journal write path
  already refused unsafe dirs).
- Field-tested with six agent-in-project simulations (agent sees ONLY the
  exported AGENTS.md): unprompted saves, correction-not-duplication,
  cross-session recall + reinforcement, zero junk on trivia, automatic
  mid-session consolidation at the signal threshold. 175 tests.

## 6.1.3 — 2026-07-04

- **Windows: reads retry transient PermissionError too** — a reader that
  opens graph.json in the instant another process is os.replace-ing it
  gets a transient sharing violation on Windows. The write and lock
  paths already retried; the unlocked read path (load + merge re-read)
  now does as well — closing the third and last member of the same
  sharing-violation family (windows-latest 3.9 CI caught 7/8 parallel
  confirms). The parallel-confirm test now also asserts every process
  exit code, so the next failure names its cause directly.


## 6.1.2 — 2026-07-04

Second verification wave (two fresh independent verifiers). Wave-1 fixes
held under 5× stress reruns; two new findings, both fixed same-day:

- **Confirm racing dream no longer loses the increment** (reproduced
  20/25 trials): decay only changes salience, so it now records
  weight-only updates applied to the fresh disk copy (min-merge — a
  concurrent confirm's boost wins the tie) instead of whole-copying a
  stale node over concurrent counters.
- **Export can no longer destroy user text that QUOTES the guard-marker
  syntax** (three reproduced variants, worst: a CLAUDE.md documenting
  mind itself lost its rules silently). Our block is now identified
  structurally — a BEGIN marker whose body starts with the exact
  generated ACTIVE header — never by bare marker strings; user-quoted
  markers, a lone END-marker file, and text between marker-like blocks
  all survive re-exports byte-for-byte (three regression tests).
- Windows CI: the os.replace retry budget under contention raised
  1s → 10s (one 3.9 runner starved with 12 parallel writers).
- Also verified clean by the wave: --at boundary semantics (11 probes),
  entity/why on 3-deep supersession chains, 30 dreams on a 500-node
  graph (append-only journal, idempotent promotions, bounded conflict
  edges, 0.3s max), and all suites/benches.
- 165 tests.


## 6.1.1 — 2026-07-04

First verification wave on 6.1.0 (three parallel independent verifiers:
fix-reproduction, adversarial probing, claims-vs-reality). The fix
verifier confirmed all 12 prior fixes; the prober found two NEW real
defects; the claims checker found two stale numbers. All fixed:

- **Concurrent confirms all count now**: two processes that both loaded
  access_count=N and both confirmed used to land N+1 (20 parallel
  confirms landed as 8–14). Reinforcement is now recorded as a DELTA and
  re-applied on the fresh disk copy inside the locked merge — 20
  parallel CLI confirms land exactly 20 (regression-tested at 8-way in
  CI and 2-instance in unit).
- **"who am I" answers again**: the 6.1.0 short-token fallback disarmed
  the empty-query identity fallback. The identity fallback now keys off
  "had any real content keys", and who/whoami/mine joined the English
  pronoun set — who am I / whoami / what do I do / tell me about myself
  all reach the name fact (regression test).
- access_count survives repair as an int (was drifting to float).
- Docs: Arabic seed count corrected to 83 (one "~90" survived 6.1.0);
  mutation kill-rate stated as the deterministic seeded 43% (was "~46%");
  Arabic README gained the last missing EN sections (reinforcement loop,
  agents-integration, development, contributing + Brain Memory credit,
  OpenClaw comparison row).
- 161 tests.


## 6.1.0 — 2026-07-04

The discrimination release. Three independent external audits (Opus, GLM,
Codex) of 6.0.2 produced ~25 findings; every runnable claim was reproduced
first, then fixed with a regression test — plus a new benchmark class so
the deepest finding can never silently return.

- **Identity-key pollution fixed** (both audits' #1): a stored fact that
  merely CONTAINED "name"/"اعمل" inherited full identity keys and
  outranked the user's actual name on "what is my name?" queries — in
  both scripts. Stored facts now earn identity keys only for explicit
  first-person identity statements; queries keep the broad fallback.
  Three cross-script regression tests reproduce the audits' exact cases.
- **Recall discrimination hardened**: co-occurrence expansion now applies
  to RARE terms only (frequent terms have direct hits; expanding them
  smeared distractor vocabulary onto unrelated facts), pure identity
  questions skip the char-gram rerank (it rewards token repetition, e.g.
  "file name … class name"), and missing basic stopwords (is/my/our/…)
  were added. Head rerank recalibrated to `base × (1 + sim)` so fusion
  rank keeps real weight.
- **New `bench/discrim.py` (in CI, gate ≥ 0.85)**: recall@1 against
  lexically COMPETING distractors — the thing the audits rightly said
  1.00-on-clean-noise never measured. Current score 12/12.
- **Concurrent field-freshness (GLM #2)**: the save merge now overwrites
  the disk only with nodes THIS session actually touched — a stale
  untouched copy in process B can no longer erase process A's
  confirmation. Reproduced, fixed, regression-tested.
- **Arabic stemmer (GLM #3)**: broken-plural dictionary is consulted on
  the FULL word before prefix stripping — كلمة/كلمات unify again (the ك
  is a root letter); dictionary extended (وظيفة/وظائف, رسالة/رسائل,
  جدول/جداول).
- **Short-token black hole (Opus #2)**: "db ai os" used to be stored
  unreachable (zero keys); such texts now index their short tokens and
  are recallable verbatim.
- **Load-path caps (GLM #4)**: MAX_TEXT_CHARS and key-list caps are
  enforced in `_repair_nodes` too — one oversized node in a synced
  graph.json no longer defeats the write-path cap.
- **`why` outlives the graph (Codex)**: for pruned ids it now answers
  from the permanent journal (status PRUNED + full lineage) instead of
  refusing.
- **Durability completed (Codex)**: atomic writes fsync the destination
  DIRECTORY after rename on POSIX (a rename alone can be lost on power
  failure); quarantine filenames now carry microseconds + pid, so two
  corruptions in the same second both survive.
- **Bidi hardening (GLM #6)**: RTL/LTR override and isolate controls
  (U+202A–E, U+2066–69) are stripped with the other control characters —
  they could spoof text in the exported agent files.
- **Bounded Windows lock wait (GLM #7)**: ~3 minutes, then a clear error
  — a hung lock holder can no longer livelock writers forever.
- **Boundary containment (GLM #8)**: the parent-symlink walk now REFUSES
  a path that never crosses the trust boundary instead of silently
  passing.
- **Read-side guards (GLM #9)**: `status` applies the signals
  symlink/size guard; the archive APPENDS (O_APPEND) instead of
  rewriting itself per prune batch; journal reads are tail-capped at
  10 MB with a note.
- **Docs made exact**: Arabic install is pinned + integrity-checked like
  English (it wasn't — worse security for Arabic readers, in the tool's
  own words unacceptable); Arabic README got the comparison table,
  discrimination section, and scope/limitation lines; test counts
  unified (158); "420 fuzz cases" now says CI runs the 160-case quick
  set; concept-seed count stated exactly (83); SECURITY.md references
  real test classes; naive-local-time and md5-id limits documented; the
  exit-code contract documented; the orphaned comparison image linked.
- Suite hygiene: parallel-writer test closes its pipes (no more
  ResourceWarnings); the injectable-clock test also bans
  datetime.today()/time.time()/utcnow/fromtimestamp.
- SKILL doctrine rewritten from field feedback: memory duty is
  proactive (store 1–3 facts after every substantive task, unprompted),
  keep a rolling "current focus" fact, never ask permission for
  remember/confirm/dream/obvious housekeeping, dream daily AND at ≥ 10
  pending signals, and query memory before claiming ignorance.
- 158 tests.


## 6.0.2 — 2026-07-03

- **Windows: atomic writes retry `os.replace` on `PermissionError`** —
  a reader that momentarily holds the destination open (Python's
  `open()` doesn't grant FILE_SHARE_DELETE) made 1/12 parallel writers
  fail on the windows-latest CI matrix. POSIX never enters the retry
  loop. This was caught by the new parallel-writers regression test
  from 6.0.1 doing its job on real Windows.


## 6.0.1 — 2026-07-03

Third-audit hardening: two independent external reviews (Codex, GLM) of
6.0.0 produced 30 findings; each was reproduced-or-refuted. 16 confirmed
defects fixed (every one with a regression test), the rest triaged as
design tradeoffs now documented, and one finding refuted with a pinning
test (`entity css` DOES find tool-only facts — category keys are written
on the node).

- **Temporal correctness: re-remembering a superseded fact now starts a
  NEW validity segment** (valid_from = now). Before, the reopened fact
  kept its original valid_from, so `recall --at` claimed it was true
  during the closed interval.
- **The live save path quarantines corrupt graphs** exactly like load —
  `_save` used to treat a corrupt graph.json as `{}` and overwrite it,
  making the README promise false on one path.
- **`init` refuses a symlinked `.mind` root before any mkdir** — it used
  to create cortex/dreams directories through the symlink before failing.
- **Concurrency: per-process temp names in atomic writes** — 12 parallel
  `remember` CLI calls all succeed now (a fixed `.tmp` name made
  concurrent exporters crash each other; reproduced 4/40 failures).
- **The provenance journal appends via a single `O_APPEND` write** —
  it was the one unlocked write path; concurrent writers could
  theoretically interleave lines.
- **Free-text commands accept text starting with dashes**
  (`remember "--dry-run is safe"` used to die as an unknown option);
  the strict flag scan now applies only to dream/recall, and the
  `dream --dryrun` typo-guard still bites.
- **`entity` applies multi-word phrase normalization** («تايب سكريبت» →
  typescript) and prints `via` + the supersession pointer per fact.
- Dream-created conflict edges carry `created` timestamps too.
- Input cap: a memory is a fact, not a document — texts over 10,000
  chars are refused with guidance.
- Signals are read with the same suspicion they are written with
  (symlink + size guards on the read side).
- Malformed (non-ISO) validity strings are repaired on load instead of
  being compared lexicographically as garbage.
- `remember`/`correct` echo the cleaned text, never raw argv (terminal
  hygiene on the output side); same-text `correct` says "nothing
  changed" instead of pretending to reconsolidate.
- `why` says when it truncates the event list; `--at` bare-date
  semantics (end of that day) documented in usage.
- Docs: install snippets are pinned + integrity-checked using `python3`
  only (works on Windows — `shasum` doesn't exist there); README states
  the closed-fact retention window and the journal's
  availability-over-completeness tradeoff plainly; SECURITY line count
  updated.
- 145 tests.


## 6.0.0 — 2026-07-03

The provenance & time release — a major version because `correct` changes
meaning: facts are now **closed, never erased**. Prompted by a public
critique that was largely right: mind answered "what relates to this?"
brilliantly but could not answer "where did this fact come from, and is
it still true?". Now it answers both, deterministically, still in one
stdlib file.

- **Write-time provenance**: every node records `origin` (`by` /
  `session` from the `MIND_BY` / `MIND_SESSION` env vars, and the command
  it came through). Every mutation — remember, link, confirm, correct,
  prune — appends to **`.mind/journal.jsonl`**, an append-only log that
  is never rotated and never cleared (unlike `signals.jsonl`, which is
  session telemetry and always was). Even pruned facts keep their lineage.
- **Truth validity, separate from attention**: nodes carry
  `valid_from`/`valid_to`. `weight` (Ebbinghaus decay) is *salience*;
  validity is *truth* — **forgetting never falsifies**. Only an explicit
  `correct` closes a fact.
- **`correct` is now temporal fusion**: the wrong fact gets
  `valid_to = now` + `superseded_by`, the corrected fact inherits its
  edges and history, and a timestamped `supersedes` edge records the
  state transition ("we were on MySQL, we moved to Postgres" stays
  queryable). Recall, working memory, clustering, and the contradiction
  scan all exclude closed facts; re-`remember`ing a closed fact re-opens
  it (explicit re-assertion wins).
- **New commands**: `why <id>` (origin, validity interval, prior texts,
  supersession links, journal events); `entity "term"` (every fact about
  a normalized term — current and superseded, with intervals);
  `recall "q" --at YYYY-MM-DD` (what was true *then*).
- **Edges carry `created` timestamps** (uni-temporal).
- Superseded facts archive after the grace window regardless of
  confirmations — they are closed states, not competing memories.
- Backward compatible on disk: pre-6.0 graphs load with honest defaults
  (`origin: unknown`, validity open since creation).
- The new journal write goes through the same symlink/parent-boundary
  checks as every other file (the suite caught the gap immediately).
- Honest scope, stated in the README: entity resolution is *lexical*
  (normalization + stemming + concept seed unify spellings, inflections,
  and AR↔EN variants); pronouns/free descriptions need a model and are
  out of scope. Credit given where due: Graphiti's bi-temporal model is
  the reference point — this is the zero-dependency, deterministic take.
- 134 tests (12 new provenance/validity tests).

## 5.6.0 — 2026-07-02

The languages release: mind is engineered for English + Arabic — this
release makes it *measured* on ten languages.

- **Script-aware tokenizer**: scripts written without spaces (CJK
  ideographs, hiragana/katakana, Hangul, Thai) are now indexed as
  character bigrams — the standard search-engine technique — instead of
  swallowing a whole phrase as one 3+-char "word". Chinese and Japanese
  recall went from **3/6 (misses returned nothing at all) to 9/9** with
  Korean included; Korean's common 2-syllable words used to be dropped
  entirely by the 3-char floor. One shared `_tokenize()` now feeds every
  indexing path (keys, co-occurrence, embeddings, destructive-op gates).
  Space-separated scripts are byte-for-byte unaffected (EN/AR suite,
  benchmark, and soak all unchanged).
- **New multilingual benchmark** (`bench/multilang.py`, runs in CI):
  recall@1 measured on 8 languages the tool was never tuned for, each
  against distractor noise — French, German, Spanish, Russian, Turkish,
  Chinese, Japanese, Korean: **24/24**. Gates: every language ≥ 2/3,
  overall ≥ 0.9. Honest scope note in the README: 3 queries per language
  is a smoke benchmark; no stemming/stopwords outside EN/AR; Thai is
  tokenized but not yet benchmarked.
- 122 tests (4 new: bigram tokenizer contract, Chinese/Japanese recall,
  Korean 2-char word indexing).

## 5.5.0 — 2026-07-02

The capability + verification release: recall gains a concept layer (the
benchmark is now 20/20), Windows gets real file locking, and two new
harnesses — a fuzzer and a mutation tester — each caught real defects on
their first run that six earlier audit rounds had missed.

- **Concept seed (recall capability): ~90 curated tool→category mappings**
  (tailwind→css, hetzner→cloud, sentry→errors, jwt→auth...), applied to
  memories and queries alike, so a question asked by category finds a
  memory that only names the tool — the benchmark's one standing miss.
  recall@1 is now **1.00** at 100 and 1,000 nodes (was 0.95). Polysemous
  words (black, express, spring, phoenix, prettier, oracle) are excluded
  by design: a false category on an everyday sentence is worse than a
  missed synonym. IDF keeps category keys from outranking exact matches
  (regression-tested).
- **Real file locking on Windows** (#9 — thanks @therealclvn):
  `msvcrt.locking` fallback when `fcntl` is unavailable, wrapped in a
  retry loop so it blocks indefinitely under contention exactly like
  `flock` (`LK_LOCK` alone gives up after ~10s with `OSError`); the
  locked read-merge-write path is preserved instead of degrading to
  atomic-write-only saves. Simulated-backend + contention regression
  tests; exercised for real on the `windows-latest` CI matrix.
- **Fuzzer** (`bench/fuzz.py`, seeded, deterministic, runs in CI): 420
  adversarial cases against the graph file and the CLI. Its first run
  caught a real defect: **the read-merge-write imported raw disk content
  past all of `_load`'s validation**, so one corrupt `graph.json` (a
  hand-edit, a buggier tool) poisoned a healthy session's next save.
  Node/edge repair is now a single shared path used by both load and
  merge; NaN/Infinity are repaired too (`float()` accepts them), history
  must be a list, edge weights are clamped finite.
- **Mutation tester** (`bench/mutate.py`): AST-level first-order defects
  (flipped comparisons/boolean ops/arithmetic, nudged constants), full
  suite run per mutant. First run: raw kill rate 33% on a 120-mutant
  sample — exposing **17 behaviors the suite never actually pinned**, each
  now locked by a dedicated test (edge weight must influence ranking, two
  confirmations protect a memory forever, stability math, full spreading
  radius, exact CLI exit codes, exact relation storage, TOP_K, hot-list
  cap...). Raw kill rate after: 46%; surviving mutants are triaged in the
  tool's output — unreachable `get()` defaults, display-only constants,
  and ranking-calibration values guarded by the CI benchmark gate
  (recall@1 ≥ 0.9) rather than by unit assertions.
- **Boundary fix (found by the new kill tests): through a symlinked
  `.mind/` root, the lock file was created outside the project** — the
  one write that escaped. The lock file's parent chain is now checked
  before creation; nothing is written through a symlinked root.
- **Dream journal accumulates**: a second dream on the same date appends
  its cycle to the day's journal instead of silently replacing it.
- Honest soak display: "hot slots" now counts only the Hot-memories
  section — 8/8 core facts, not 8/23 against instruction bullets.
- 118 tests (was 94).

## 5.4.2 — 2026-07-02

Full-repository precision review (every file, line by line). Six defects
found and fixed, each with a regression test that fails on 5.4.1 (94 total):

- **`correct` skipped the control-char sanitizer** that `remember` applies:
  a corrected memory could carry ANSI escapes back to the terminal on
  recall (violating the SECURITY.md hygiene claim) and was stored under an
  id `remember()` would never produce, so re-remembering the same cleaned
  text created a duplicate node. An empty or control-chars-only
  replacement also silently blanked the memory — both now refused (CLI
  validates too).
- **`link` accepted self-links**, creating a self-loop edge that fed a
  node its own activation on every spreading hop and silently inflated
  its rank.
- **Working memory grew to 4× its documented size**: the hot-list budget
  applied a leftover ×4 token→char conversion on top of a constant already
  expressed in characters, so ACTIVE.md could reach ~800 tokens while the
  README promised ~200.
- **Key extraction was nondeterministic across machines**: the [:24]
  truncation iterated a set, whose order varies with str-hash
  randomization — identical `remember` calls could store different key
  subsets per run/machine. Keys now preserve first-appearance order.
- **A non-string timestamp crashed `dream`**: `decay` caught only
  ValueError, so a hand-edited numeric `last_accessed` raised TypeError.
  Repaired on load and tolerated in decay, like every other field.
- Cosmetic: redundant dict copy in the save merge.

## 5.4.1 — 2026-07-02

Follow-up to the 5.4.0 edge-merge fix (caught by a third adversarial pass):

- The `_pruned_edges` stripping ran *after* live edges were merged, so it
  could clobber an edge legitimately (re)created the same session — most
  visibly the `possible-conflict` link `dream` creates when a conflicting
  pair's old edge decayed the same night. Stripping now happens on the disk
  copy *before* the live merge, and `_deleted`/`_pruned_edges` are cleared
  after each successful save so a stale prune record can't poison a later
  write. Two regression tests added (88 total).

## 5.4.0 — 2026-07-02

Second adversarial audit (an Opus-4.8 fleet: 17 reviewers with distinct
methodologies, each finding independently reproduced-or-refuted, plus a
completeness critic). Every confirmed defect fixed with a regression test
(86 tests total):

- **Data-loss (critical): `export_to_agents` could destroy a user's whole
  CLAUDE.md/AGENTS.md/GEMINI.md** if it merely contained the substring
  "mind working memory" or began with "# ACTIVE.md" — no markers, no
  backup, no warning. The stale-block heuristic is now a strict structural
  match on our exact generated header, so real user files that mention the
  tool are preserved.
- **Security: parent-symlink escape.** `_atomic_write` guarded only the
  final path; a symlinked `.mind/dreams` or `.mind/cortex` let dream/promote
  overwrite arbitrary files outside the project. A parent-directory symlink
  walk (`boundary=`) now protects every internal write, and dream/promote
  degrade gracefully instead of crashing on an unsafe dir.
- **Weight inflation: a future `last_accessed`** (clock skew / cross-machine
  sync — this is synced memory, `_now()` is naive local time) made decay's
  retention exceed 1.0 and inflated a node's weight unboundedly and
  permanently. Decay now clamps to `[0,1]` and treats future timestamps as
  fresh.
- **Edge decay was never persisted.** The "every dream weakens all edges"
  claim held in-process but the CLI reloads from disk each run, so edges
  never actually decayed; dream now saves after the edge pass. Fixing this
  surfaced a latent **merge-revival bug** (a pruned edge resurrected from
  the disk copy because edge deletions weren't tracked) — now tracked and
  stripped in the read-merge-write.
- **Corrupt-graph robustness.** A non-numeric `weight`, or a `keys` field
  that's a bare string or contains a non-string element, no longer bricks
  every command — numeric fields are coerced and keys are validated on load.
- **Phantom edges: `link` hashed raw text** while `remember` hashed the
  control-char-cleaned text, so an edge on text with control chars landed on
  a non-existent id and was dropped on reload. Both now share one
  sanitizer.
- **Honest docs:** the soak now also shells out to the real CLI (argv +
  disk-reload path); the "light sleep — signal replay" phase is documented
  as the telemetry it actually is, not a replay.

## 5.3.0 — 2026-07-02

Audit-hardening release. Three independent code audits (two external
models + a line-by-line review) were verified claim by claim; every
confirmed finding is fixed here with a regression test:

- **Reinforcement is now agent-reachable**: `recall` prints memory ids and
  the new `confirm <id>` command performs the bump — previously `bump()`
  existed only as a library call no agent could invoke, so "recalled often
  → hardens" could not happen through the CLI. Exported agent
  instructions teach the confirm loop.
- **Edge weights are now dynamic**: every dream weakens all edges
  (synaptic homeostasis, ×0.95), `confirm` restrengthens the confirmed
  node's edges; unconfirmed connections prune after ~45 dreams —
  synaptic pruning was previously dead code for `link` edges (always 1.0).
- **Durability**: fsync before rename in all atomic writes (power-loss
  safety was previously overstated); the lock file is opened O_NOFOLLOW
  (a symlinked `graph.json.lock` could truncate its target).
- **Archive guarantee**: pruning now archives first and skips deletion
  entirely if the archive is unwritable (a symlinked `archive.md`
  previously caused silent data loss).
- **Graph robustness**: edges referencing missing nodes are dropped on
  load and filtered in recall (orphan edges could crash recall with
  KeyError after partial corruption).
- **`correct` merge semantics**: correcting a memory into the text of an
  existing memory now merges histories/edges/reinforcement instead of
  clobbering the existing node.
- **Cortex collision safety**: two topics sanitizing to the same filename
  no longer overwrite each other (content-hash suffix).
- **Multi-word normalization fixed** ("تايب سكريبت" → typescript now
  matches — phrases are replaced before tokenization); `link` relations
  are sanitized like memory texts.
- CI: actions pinned by commit SHA, least-privilege permissions,
  Windows added to the test matrix.
- 77 tests. Independent-audit claims that did **not** reproduce are
  documented in the repo discussions rather than silently ignored.

## 5.2.0 — 2026-07-02

- **New export targets** (first community contribution — thanks
  [@Abhinav-0311](https://github.com/Abhinav-0311), #6): `.cursorrules`,
  `.windsurfrules`, `.clinerules` and `.roo/rules/mind.md` (Cursor,
  Windsurf, Cline, Roo), with symlink-parent protection for nested paths
  and Windows-portable atomic writes.
- Follow-up hardening on top of #6: tool dotfiles are **adopted, not
  imposed** — written only when the project already has the rule file (or
  a `.roo/` directory), so fresh projects stay clean with the three
  canonical files; dangling-symlink parents are now skipped too
  (`exists()` follows links and missed them).

## 5.1.0 — 2026-07-02

Soak-hardened forgetting. A new 180-day simulated-usage soak test
(`bench/soak.py` — the real code driven through an injected clock) caught
two calibration bugs that unit tests could not:

- **Facts needed monthly died before their first recall** (pruned by the
  nightly dream one day before the scheduled recall). Fix: a 45-day grace
  window — no memory is pruned within 45 days of its last access. Weight
  still decays during grace, so unproven memories fade from rankings.
- **Decayed weight vetoed exact matches**: a strongly-matching but aged
  memory ranked below fresh noise, so it could never earn its first
  reinforcement. Fix: weight now biases ranking (floor 0.35) instead of
  multiplying it to zero.
- Stability per confirmed recall raised to ~two weeks (was 5 days).
- Pruned memories are now archived to `.mind/archive.md`, never destroyed.
- Soak results at day 180: core-fact survival 15/15 across daily/weekly/
  monthly tiers, stale junk surviving 0/256, graph bounded, recall 0.37 ms.
  The soak now runs in CI.

## 5.0.0 — 2026-07-02

First public release. The tool was developed and battle-tested privately
through 17+ iterations (v1.0 → v4.x) on real agents (Codex + MiniMax-M3),
with every defect below found by live testing, then consolidated into a
single zero-dependency file for release:

- **Recall**: spreading activation (≤3 hops) + IDF + Reciprocal Rank Fusion;
  offline hash-embedding re-rank; pattern completion (fuzzy cue fallback);
  pattern separation (near-duplicate results diversified out of top-k).
- **Forgetting**: Ebbinghaus curve `R = e^(−t/S)`; stability grows with
  confirmed recalls; `recall` is pure read — reinforcement only via `bump()`.
- **Dreaming**: light (session signals) → deep (decay + synaptic edge
  pruning) → REM (deterministic clustering → cortex promotion +
  contradiction flagging). `--dry-run` previews everything.
- **Reconsolidation**: new `correct` command — rewrite a wrong memory,
  history preserved on the node, confidence lowered until re-confirmed.
- **Portability**: guard-marked export to AGENTS.md / CLAUDE.md / GEMINI.md,
  idempotent, preserves user content, refuses symlinks, atomic writes.
- **Fixed during consolidation**: offline dream promotion (previously
  required a network embedding backend — clusters never promoted offline),
  cortex filename sanitization bug, duplicated key-extraction loop, and a
  user-content duplication bug on re-export (caught by the new test suite).
- 58 unit tests (stdlib only) + reproducible benchmark (`bench/bench.py`).
