# What is in here, and why it is not ours

`ibmf1-practice.xml` is a verbatim copy of the other half of this project's own
practice race configuration, kept so that
`tests/test_team_preset.py` can prove Apex's `apexibmf1.xml` still describes the
same race. Without a copy, that test would need their repository checked out to
run, which means it would not run in CI and would therefore not be a test.

| | |
|---|---|
| Source | `github.com/UOBGraduate/IBMF1`, `apps/player-kit/config/practice.xml` |
| Taken from | `bda5455` (2026-09-02) |
| Blob | `23f6dea8a80e2610d92391f727b804f416bf4ee9` |
| Last upstream change to it | `df93842` "Reorganize repository by runtime ownership", 2026-09-01 |
| Declares itself | `ibmf1-practice-v5` |

It is copied, not edited. If their race changes, this file is what has to be
refreshed first -- and the test will fail until Apex's preset is brought back
into line, which is the entire point of keeping it.

To refresh it against a checkout of theirs:

```bash
git -C <their checkout> show origin/main:apps/player-kit/config/practice.xml \
  > integrations/torcs-1.3.9/reference/ibmf1-practice.xml
```

`tests/test_team_preset.py` also accepts `IBMF1_PRACTICE_XML` pointing at a live
file, so the same contract can be checked against their working copy without
committing anything.

Nothing here is loaded at runtime and nothing here is shipped in the installer.
