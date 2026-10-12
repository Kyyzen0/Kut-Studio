# Kut-Studio website (OrigoHub)

Static French website for https://kut-studio.pages.dev/.

This project deploys to the **existing Cloudflare Pages project** called `kut-studio`, including Direct Upload projects. No new Pages project is necessary.

## One-time setup
1. In Cloudflare, generate an API token scoped to your account with **Cloudflare Pages: Edit** permission.
2. Copy the **Cloudflare Account ID** from your account dashboard.
3. On GitHub, open **Kut-Studio → Settings → Secrets and variables → Actions → New repository secret**.
4. Add `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID` (as separate secrets). Never put these values in files or chats.
5. After this pull request is merged, run **Actions → Deploy Kut-Studio website to Cloudflare Pages → Run workflow**.

Subsequent changes to `website/**` on main deploy automatically. App code changes do not trigger deployments; the site detects the latest *published* release number via GitHub's public API.

The brand mark is a **temporary typographic placeholder**, not the original Kut-Studio logo. Replace it once the approved logo is available.
