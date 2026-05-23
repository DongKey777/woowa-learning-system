# Phase 9 Migration Plan (executed only after `bin/phase9-gate` returns READY)

This is a checklist, not an automated script. Each step is reversible until
step 5 (legacy delete). Steps 1-4 leave legacy untouched.

## Preconditions (required before step 1)

- `bin/phase9-gate` returns READY (verdict line)
- All RunPod-built metrics in `reports/phase9_rag_eval.json` exceed legacy
- 5-mission coach-run e2e in `reports/phase9_coaching_eval.json` passes
- A learner-machine sanity run on the new system completed without errors

If any precondition fails, choose one:

- **(a) weak-component recovery** — port the failing legacy module verbatim
  into `core/legacy_<name>.py` and reroute. Document in
  `reports/phase9_absorb_<name>.md`.
- **(b) partial absorb** — keep both systems live, route by intent
  (e.g. CS Q&A to new, peer PR to legacy) via `bin/ask` mode dispatch.
- **(c) revert** — abandon redesign, document in
  `reports/phase9_revert.md`, return to incremental cards in legacy.

## Step 1 — Snapshot legacy

```bash
cd /Users/idonghun/IdeaProjects/woowa-learning-hub
git tag legacy-pre-redesign-$(date +%Y%m%d)
git push --tags
```

Captures rollback point.

## Step 2 — Rename legacy runtime state

```bash
mv state/cs_rag state/cs_rag_legacy_$(date +%Y%m%d)
mv knowledge/cs/contents knowledge/cs/contents_legacy_$(date +%Y%m%d)
```

Old daemon refuses to start against renamed state — natural cutover signal.

## Step 3 — Wire bin/rag-ask → bin/ask alias

In legacy hub `bin/`:

```bash
mv bin/rag-ask bin/rag-ask.legacy
cat > bin/rag-ask <<'EOF'
#!/usr/bin/env bash
exec /Users/idonghun/IdeaProjects/woowa-learning-system/bin/ask "$@"
EOF
chmod +x bin/rag-ask
```

Repeat for `coach-run`, `learn-drill`, `learn-response-quality`,
`learn-pr-retro`, `learn-self-assess` — each becomes a thin redirect.

## Step 4 — Daemon fingerprint refresh

```bash
# legacy daemon will fail to start against renamed state — clean stop
bin/rag-daemon stop || true
rm -f state/rag-daemon.{json,log,pid}
```

## Step 5 (point of no return) — Delete legacy code

Only after 1 week of learner use on new system without regression:

```bash
cd /Users/idonghun/IdeaProjects/woowa-learning-hub
rm -rf scripts/learning/rag/r3/
rm -f scripts/workbench/core/interactive_rag_router.py
rm -f scripts/workbench/core/lexicon.py
rm -f scripts/workbench/core/response_quality.py
rm -f scripts/learning/rag/corpus_lint.py
```

Removes ~5000 LOC. Estimated drop:
- Legacy runtime: 80K → 75K LOC (-6% from this step)
- Combined with new system: total platform 80K → 1856 LOC (-97.7%)

## Step 6 — Update docs

Update `CLAUDE.md` + `AGENTS.md` in legacy hub:
- Section "Recommended Model" stays.
- Section "Interactive Learning RAG Routing" → "Delegated to woowa-learning-system."
- Section "RAG performance closed loop" → archived to `docs/legacy/`.

## Step 7 — Commit + tag

```bash
cd /Users/idonghun/IdeaProjects/woowa-learning-hub
git add -A
git commit -m "Phase 9: legacy RAG/coaching delegated to woowa-learning-system"
git tag legacy-post-redesign-$(date +%Y%m%d)
git push && git push --tags
```

## Rollback (any time before step 5)

```bash
git checkout legacy-pre-redesign-<date> -- .
mv state/cs_rag_legacy_<date> state/cs_rag
mv knowledge/cs/contents_legacy_<date> knowledge/cs/contents
mv bin/rag-ask.legacy bin/rag-ask  # repeat for each renamed alias
```

After step 5, rollback requires `git revert` of the deletion commit +
re-running RunPod build to repopulate `state/cs_rag/`.
