# Kut-Studio website (OrigoHub)

Static website for https://kut-studio.pages.dev/ in three languages:

- French: `/` (`index.html`)
- English: `/en`
- Spanish: `/es`

Shared files: `style.css`, `script.js`, `assets/interface.png`, `assets/origohub-green.png`.

This project deploys to the **existing Cloudflare Pages project** called `kut-studio`, including Direct Upload projects. No new Pages project is necessary.

The pages reproduce the public site that was online before the 12 October 2026 placeholder deploy: OrigoHub emblem, language switch, and the same sections. English and Spanish HTML were not in the public archive; they follow the French page, and the workflow steps use the English and Spanish copy already stored in `script.js`.

The project version shown on the page is **0.5.0** (`core/app_version.py`). The download buttons point at the latest **published** GitHub release, currently **v0.4.2**, because v0.5.0 has no release assets yet.

## One-time setup
1. In Cloudflare, generate an API token scoped to your account with **Cloudflare Pages: Edit** permission.
2. Copy the **Cloudflare Account ID** from your account dashboard.
3. On GitHub, open **Kut-Studio → Settings → Secrets and variables → Actions → New repository secret**.
4. Add `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID` (as separate secrets). Never put these values in files or chats.
5. After this pull request is merged, run **Actions → Deploy Kut-Studio website to Cloudflare Pages → Run workflow**.

Subsequent changes to `website/**` on main deploy automatically. App code changes do not trigger deployments.
