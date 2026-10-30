# Users and roles

Every page of Fragdex requires a login. Accounts are managed by admins under **Users**.

## Roles

| | Viewer | Editor | Admin |
|---|:-:|:-:|:-:|
| Search, view players, export CSV and .fdx | :material-check: | :material-check: | :material-check: |
| Import files, add and delete records | | :material-check: | :material-check: |
| Edit notes and tags, manage the Tags page, refresh Steam data | | :material-check: | :material-check: |
| Manage users, Settings, delete players | | | :material-check: |

Give people the lowest role that works for them. The database holds IP addresses, which are personal data in many countries, so keep the number of accounts down to people who need them.

## Add a user

Under **Users → Add user**, enter:

- **Username**: 2–40 characters, letters, numbers and `_ . @ -`.
- **Password**: at least 10 characters. Send it to the person over a private channel and ask them to change it under **Account**.
- **Role**.

## Change a user

Each row in the user list can be changed in place; press **Save** on that row afterwards.

- **Role**: promote or demote.
- **Disabled**: blocks the login without deleting the account. A disabled user's sessions stop working too.
- **Reset password**: type a new password for that user.

The bin icon deletes the account. Notes, tags and records the user added stay.

Fragdex prevents locking yourself out: you cannot disable or delete your own account, and the last admin cannot be demoted, disabled or deleted.

## Your own account

**Account** (your username in the top-right corner) shows your role and lets you change your password. Logins last `SESSION_DAYS` days (7 by default); **Log out** ends the session.

## The first admin

The first admin account is created on the very first start, from `ADMIN_USERNAME` and `ADMIN_PASSWORD` in `.env`. If `ADMIN_PASSWORD` is empty a random password is generated and printed in the log (`docker compose logs app`). After that, these two settings are ignored, and you can remove the password from `.env`.

## Forgotten admin password

If no admin can log in any more, set a new password from the server's command line, in the Fragdex folder:

```bash
docker compose exec app python -c "import sys; from fragdex import auth, db; db.connect().execute('UPDATE users SET password_hash=?, disabled=0 WHERE username=?', (auth.hash_password(sys.argv[2]), sys.argv[1]))" admin 'a-new-long-password'
```

Replace `admin` with the username and choose a new password of at least 10 characters. The account is also re-enabled if it was disabled.
