# Third-party components

ArtiLED's own code in this repository is proprietary (see the Copyright section
of `README.md`).

**This version bundles no third-party code.** It uses only what Home Assistant
itself provides (its shared aiohttp session, voluptuous).

When a third-party library is bundled (the dashboard, slice S2, may add browser
libraries), it keeps its own licence: its full text goes in `LICENSES/`, it is
listed in a table here, and nothing is minified by us, re-headered or
relicensed — the Hub integration's posture, see `ArtiLED_Hub_HA/THIRD-PARTY.md`.

⚠ **Never package Font Awesome Pro icons.** The Hub integration once published
3,241 of them and the repository had to be deleted and rebuilt. Icons are read
from the customer's own Control at runtime instead.

⚠ A licence answer is only valid for the distribution model it was given for.
Re-check every bundled dependency when a package becomes public.
