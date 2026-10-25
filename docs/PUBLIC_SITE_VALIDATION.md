# Public site validation — October 25, 2026

- Existing repository regression suite: **215 tests passed**.
- Public export/build suite: **7 tests passed** (field allowlist, stable identities, invalid records, retained historical status, map validation, and preservation of the previous bundle when a required source group is missing).
- Public browser suite passed at a repository subpath: search, pagination, public profile links, refresh and back/forward navigation, device-local watchlists, separate browser profiles, mobile layout, keyboard map navigation, unsafe-link rejection and rendering source markup as text.
- JavaScript syntax checking and Python compilation passed.
- Fresh federal collection: **435 profiles** (343 MPs and 92 senators).
- Fresh map collection: **342 district features**. Marc Miller's Ville-Marie—Le Sud-Ouest—Île-des-Sœurs record did not match a boundary and remains available in the directory.
- The real-data bundle built successfully with both required source groups. Browser checks confirmed all 435 profiles and all 342 map features load without JavaScript exceptions.

The test fixtures are synthetic and temporary. The real published bundle is generated from collector outputs; fixtures are not used as public data. Generated datasets are ignored by Git and created by the deployment workflow.

This report describes local validation. GitHub Actions results and the actual Pages deployment must be checked separately. A successful build does not mean the public URL has been deployed.
