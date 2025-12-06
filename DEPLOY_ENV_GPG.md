## Encrypted production env

We no longer commit plaintext `.env.prod`/`.env.dev`. `.env.prod` should be stored as a GPG‑encrypted blob (`.env.prod.gpg`) in the repo, and decrypted on the server during deploy.

### Encrypt locally
1. Ensure `.env.prod` exists locally (git‑ignored).
2. Run:
   ```bash
   gpg --symmetric --cipher-algo AES256 .env.prod
   ```
   - Enter a strong passphrase.
   - This produces `.env.prod.gpg`.
3. Commit **only** `.env.prod.gpg` (plaintext stays untracked).

### Deploy prerequisites
- Set GitHub secret `ENV_FILE_GPG_PASSPHRASE` to the same passphrase used above.
- The deploy workflow expects `.env.prod.gpg` in the repo and decrypts it with `gpg` on the server.

### Notes
- For local dev, keep using your untracked `.env.dev`/`.env` files.
- If you rotate secrets, re-encrypt and recommit `.env.prod.gpg`.

ssh -i ~/.ssh/swimbuddz_deploy_key deploy@161.35.209.68