# Tampermonkey Userscript Install — Troubleshooting

The main install script (`import-tampermonkey-userscripts.ps1`) opens Edge with
`--profile-directory` to trigger Tampermonkey's install prompt. If Edge opens the
wrong profile (common when Edge is already running), use the workaround below.

## Workaround: Import from URL via Tampermonkey Dashboard

1. **Start the local server with `-NoLaunch`** so it prints the URL without
   opening a browser:

   ```powershell
   # Serve one script at a time on a unique port:
   powershell -ExecutionPolicy Bypass -File .\scripts\import-tampermonkey-userscripts.ps1 `
     -Path .\scripts\m365-agents-add-contract-policy-expert.user.js `
     -NoLaunch `
     -Port 8766

   powershell -ExecutionPolicy Bypass -File .\scripts\import-tampermonkey-userscripts.ps1 `
     -Path .\scripts\m365-agent-ledger-add-contract-policy-expert.user.js `
     -NoLaunch `
     -Port 8767
   ```

   Each command prints a URL like
   `http://127.0.0.1:8766/m365-agents-add-contract-policy-expert.user.js?v=...`

2. **Open Edge in the correct profile manually** (switch profiles from the
   profile icon in the toolbar).

3. **Go to Tampermonkey → Dashboard → Utilities → Import from URL.** Paste the
   URL printed by the server and click Install/Import.

4. Repeat for each userscript URL.

## Why this happens

Edge ignores `--profile-directory` when an Edge process is already running with a
different profile. Closing *all* Edge windows before running the script is one
fix, but using `-NoLaunch` + Tampermonkey Dashboard import is more reliable and
doesn't disrupt your browsing.

## Tips

- Use separate `-Port` values if you need multiple servers running simultaneously.
- The server stays alive for 10 minutes by default (`-KeepAliveSeconds 600`).
- You can also use `-DefaultBrowser` to open in whatever browser handles HTTP
  links (useful if Tampermonkey is in a non-Edge browser).
