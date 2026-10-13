# Kut-Studio website (OrigoHub)

Static website for https://kut-studio.pages.dev/ in three languages:

- French: `/` (`index.html`)
- English: `/en`
- Spanish: `/es`

Shared files: `style.css`, `script.js`, `assets/interface.png`, `assets/origohub-green.png`.

This project deploys to the **existing Cloudflare Pages project** called `kut-studio`, including Direct Upload projects. No new Pages project is necessary.

The pages reproduce the public site that was online before the 12 October 2026 placeholder deploy: OrigoHub emblem, language switch, and the same sections. English and Spanish HTML were not in the public archive; they follow the French page, and the workflow steps use the English and Spanish copy already stored in `script.js`.

The project version shown on the page is **0.5.5** (`core/app_version.py`). The download buttons point at the latest **published** GitHub release, currently **v0.5.5**.

## One-time setup
1. In Cloudflare, generate an API token scoped to your account with **Cloudflare Pages: Edit** permission.
2. Copy the **Cloudflare Account ID** from your account dashboard.
3. On GitHub, open **Kut-Studio → Settings → Environments → production → Add environment secret**. Use the **`production` environment**, not repository secrets: an environment secret is only readable by a job that deploys from `main`, whereas a repository secret can be read by a workflow pushed on any branch.
4. Add `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID` (as separate secrets). Never put these values in files or chats. If the same names already exist as **repository** secrets, delete them once the environment secrets are in place.
5. After this pull request is merged, run **Actions → Deploy Kut-Studio website to Cloudflare Pages → Run workflow**.

## Security notes
- `website/_headers` sets a strict Content-Security-Policy. `style.css` and `script.js` are same-origin (`'self'`). The small inline script in each page only writes text and is allowed by its SHA-256 hash: if you edit it, update the hash (`tests/test_website_headers.py` tells you the new value). Google Fonts are allowed for the public typeface (`fonts.googleapis.com`, `fonts.gstatic.com`).
- The workflow pins every action to a full commit SHA and the Cloudflare token should be limited to **Pages: Edit** on this account only, with an expiry date.

Subsequent changes to `website/**` on main deploy automatically. App code changes do not trigger deployments.
