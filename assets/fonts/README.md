# Fonts

The faces the PDF course report is set in (roadmap item 14): the three families `docs/tokens.css`
names as `--f-display`, `--f-body` and `--f-mono`, static weights only. They are vendored so that a
report looks the same on every machine that renders it, and so a report never falls back to
whatever the host happens to have installed.

Each family's licence file sits beside it. All three are under the **SIL Open Font License 1.1**,
read from the downloaded licence texts; the OFL permits bundling and embedding with software, and
its conditions do not extend to a document set in the fonts. The Reserved Font Name for Plex is
"Plex" (IBM's copyright line) — these files are unmodified.

| File | Family, weight | Version | Source | SHA-256 |
|---|---|---|---|---|
| `InstrumentSerif-Regular.ttf` | Instrument Serif 400 | 1.000 | `github.com/google/fonts`, `ofl/instrumentserif/` | `498efd461f6ddfcb7a111bf9a565709d2085d48201d501ead960d93e84ffbb88` |
| `IBMPlexSans-Regular.ttf` | IBM Plex Sans 400 | 3.005 | `github.com/IBM/plex`, release `@ibm/plex-sans@1.1.0`, `fonts/complete/ttf/` | `975dcda37d80f038dcd143c22e33ca2d97a0cc5a929aace1c749153b0fe1afa5` |
| `IBMPlexSans-Italic.ttf` | IBM Plex Sans 400 italic | 3.005 | as above | `a9c6ef9942c49e49d11e11a6dacc0b3a087978757e9b22a06b8ac22a6400fb15` |
| `IBMPlexSans-SemiBold.ttf` | IBM Plex Sans 600 | 3.005 | as above | `a20caf8286023a6a7a85e40b1d2a4ae9fc3e3b1f9eda8f4c542dd4986af67bb1` |
| `IBMPlexMono-Regular.ttf` | IBM Plex Mono 400 | 2.005 | `github.com/IBM/plex`, release `@ibm/plex-mono@2.5.0`, `fonts/complete/ttf/` | `7c6fbddca4b700be918f5f6183d9bd4464fa427fe435f0b480d77fe2bb8c5a43` |
| `IBMPlexMono-SemiBold.ttf` | IBM Plex Mono 600 | 2.005 | as above | `f04d7c488ddf7d1fa99f2574efc3406ea4cbe17bb1af3a1ab960f84d0c96a172` |

| Licence file | Covers |
|---|---|
| `InstrumentSerif-OFL.txt` | Instrument Serif — "Copyright 2022 The Instrument Serif Project Authors" |
| `IBMPlexSans-LICENSE.txt` | IBM Plex Sans — "Copyright © 2017 IBM Corp. with Reserved Font Name 'Plex'" |
| `IBMPlexMono-LICENSE.txt` | IBM Plex Mono — the same text as the Sans licence |

Coverage, read from the files' character maps: IBM Plex Sans covers Latin, Greek and Cyrillic;
IBM Plex Mono Latin and Cyrillic; Instrument Serif, used for headings, Latin only. The container
carries no other fonts, so course text in a script the face in use does not cover may print as
empty boxes; the text is still in the PDF and still searchable.

Replacing a file here changes every report's bytes: re-run `scripts/make-sample-report.sh`.
