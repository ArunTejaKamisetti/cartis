# Deploy Cartis on Render (shareable link)

You need: a GitHub account, a Render account, and `token.json` from `Documents\cartis` (made by `python -m cartis.cli auth`).

## 1. Put the code on GitHub (private repo)
1. github.com → New repository → name `cartis` → **Private** → Create.
2. Either run, in `Documents\cartis`:
   ```
   git init && git add . && git commit -m "Cartis v1" && git branch -M main
   git remote add origin https://github.com/<you>/cartis.git && git push -u origin main
   ```
   or use "uploading an existing file" on the repo page and drag in the folder contents.
   `.env`, `credentials.json`, `token.json` and `data/` are git-ignored. **Never upload them**; with the drag-and-drop route, leave those four out.

## 2. Create the service from the Blueprint
1. dashboard.render.com → **New → Blueprint** → connect GitHub → pick `cartis`.
2. Render reads `render.yaml` and asks for the secret values:

| Key | Value |
|---|---|
| ANTHROPIC_API_KEY | from your `.env` |
| GNANI_API_KEY | from your `.env` |
| APIFY_TOKEN | from your `.env` |
| SERPER_API_KEY | from your `.env` |
| CARTIS_SHEET_ID | from your `.env` |
| GOOGLE_TOKEN_JSON | the **whole contents** of `token.json` (open it in Notepad, copy everything) |
| DEMO_PASSCODE | any word, e.g. `ken2026`; needed by anyone opening the link |

3. Apply. The first build takes ~3 minutes. The link looks like `https://cartis-xxxx.onrender.com`.

## 3. Share it
Send `https://cartis-xxxx.onrender.com/?key=<passcode>`; the key is remembered in the browser after the first visit.

Notes
- Free tier sleeps after 15 min idle; the first visit takes ~1 minute to wake. Open it once before a demo.
- The Google token from Testing mode expires 7 days after sign-in. If Sheets/Gmail stop working: run `python -m cartis.cli auth` again and paste the new `token.json` into GOOGLE_TOKEN_JSON.
- Rotate all API keys after the competition.
