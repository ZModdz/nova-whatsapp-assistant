# Step-by-Step Guide: Google Calendar OAuth 2.0 Setup (100% Free)

This guide explains how to connect JARVIS to your personal Google Calendar at **RM0 cost** and prevent your OAuth refresh token from expiring every 7 days.

> [!CAUTION]
> Never commit `credentials/client_secret.json` or `credentials/token.json` to Git! Our [`.gitignore`](../.gitignore) already blocks the entire `credentials/` folder.

---

## Step 1: Create a Free Google Cloud Project
1. Go to the [Google Cloud Console](https://console.cloud.google.com/) and sign in with your personal Google account.
2. Click the project dropdown at the top -> **New Project**.
3. Name it `JARVIS Calendar` and click **Create** (no billing account or credit card is needed).

## Step 2: Enable the Google Calendar API
1. In your new project, go to **APIs & Services** -> **Library**.
2. Search for **Google Calendar API**, click it, and click **Enable**.

## Step 3: Configure the OAuth Consent Screen (And Prevent 7-Day Token Expiry)
1. Go to **APIs & Services** -> **OAuth consent screen** (or **Google Auth Platform** -> **Branding / Audience**).
2. Select **External** user type and click **Create**.
3. Fill in the required fields:
   - **App name:** `JARVIS Personal Assistant`
   - **User support email:** Your Gmail address
   - **Developer contact information:** Your Gmail address
4. Click **Save and Continue** through Scopes.
5. Under **Test users**, add your own Gmail address.
6. **CRITICAL PRO-TIP (Prevent 7-Day Expiry):**
   - In "Testing" mode, Google revokes OAuth refresh tokens every 7 days.
   - Under **Publishing status** (on the OAuth consent screen / Audience page), click **Publish App** -> **Confirm**.
   - **Do NOT submit for verification.** For personal use by 1 account, you will simply see an *"Unverified App -> Advanced -> Go to JARVIS Personal Assistant (unsafe)"* button once when logging in, and your refresh token will now **last indefinitely** without expiring!

## Step 4: Download `client_secret.json`
1. Go to **APIs & Services** -> **Credentials**.
2. Click **+ Create Credentials** -> **OAuth client ID**.
3. Select Application type: **Desktop app**.
4. Name it `JARVIS Windows Client` and click **Create**.
5. Click **Download JSON** on the popup.
6. Rename the downloaded file to `client_secret.json` and place it inside the project's `credentials/` directory:
   `credentials/client_secret.json`

## Step 5: Authorize JARVIS (We Will Run This in Phase 4)
Once `credentials/client_secret.json` is in place, running our authentication script will open your browser once, let you grant Calendar access (`https://www.googleapis.com/auth/calendar.events`), and save `credentials/token.json` locally.
