# Kut-Studio website (OrigoHub)

Static French website for https://kut-studio.pages.dev/.

This project deploys to the **existing Cloudflare Pages project** called `kut-studio`, including Direct Upload projects. No new Pages project is necessary.

## One-time setup
1. In Cloudflare, generate an API token scoped to your account with **Cloudflare Pages: Edit** permission.
2. Copy the **Cloudflare Account ID** from your account dashboard.
3. On GitHub, open **Kut-Studio → Settings → Environments → production → Add environment secret**. Use the **`production` environment**, not repository secrets: an environment secret is only readable by a job that deploys from `main`, whereas a repository secret can be read by a workflow pushed on any branch.
4. Add `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID` (as separate secrets). Never put these values in files or chats. If the same names already exist as **repository** secrets, delete them once the environment secrets are in place.
5. After this pull request is merged, run **Actions → Deploy Kut-Studio website to Cloudflare Pages → Run workflow**.

## Security notes
- `website/_headers` sets a strict Content-Security-Policy. The inline script and style of `index.html` are allowed by their SHA-256 hash: if you edit either, update the hash (`tests/test_website_headers.py` tells you the new value).
- The workflow pins every action to a full commit SHA and the Cloudflare token should be limited to **Pages: Edit** on this account only, with an expiry date.

Subsequent changes to `website/**` on main deploy automatically. App code changes do not trigger deployments; the site detects the latest *published* release number via GitHub's public API.

The brand mark is a **temporary typographic placeholder**, not the original Kut-Studio logo. Replace it once the approved logo is available.
