# Phase 2 Setup Guide: Meta WhatsApp Cloud API, Tailscale Funnel & Ollama

This guide walks you through obtaining your **100% free (RM0/month)** credentials and exposing your local JARVIS server securely via **Tailscale Funnel**.

> [!IMPORTANT]
> **Security Rule:** Never paste your `WHATSAPP_ACCESS_TOKEN` or `WHATSAPP_APP_SECRET` into chat or commit them to Git! Edit your local `.env` file directly in your code editor.

---

## Step 1: Install Ollama & Download `qwen2.5:3b`

If the automated `winget` installation did not already complete, run these commands in PowerShell:

```powershell
# 1. Install Ollama via Windows Package Manager
winget install --id Ollama.Ollama -e --accept-source-agreements --accept-package-agreements

# 2. Restart your terminal (or refresh PATH) and pull the lightweight 3B model (~1.9 GB)
ollama pull qwen2.5:3b

# 3. Verify Ollama is running on localhost:11434
ollama list
```

---

## Step 2: Install & Configure Tailscale Funnel (Free Permanent HTTPS Webhook)

Tailscale Funnel gives your laptop a permanent `https://<your-laptop-name>.<your-tailnet>.ts.net` URL that automatically reconnects whenever you turn your laptop on or switch Wi-Fi networks (home, campus, mobile hotspot).

1. **Install Tailscale:**
   ```powershell
   winget install --id Tailscale.Tailscale -e --accept-source-agreements --accept-package-agreements
   ```
2. **Sign in to Tailscale:**
   - Open the Tailscale app from your Windows system tray (or run `tailscale up` in PowerShell) and sign in with a free personal account (Google/GitHub/Microsoft).
3. **Enable HTTPS Certificates & Funnel in Tailscale Admin Console:**
   - Go to [Tailscale DNS Settings](https://login.tailscale.com/admin/dns) and enable **MagicDNS** and **HTTPS Certificates**.
   - Go to [Tailscale Access Controls (ACLs)](https://login.tailscale.com/admin/acls) and ensure the `funnel` node attribute is enabled (Tailscale prompts you with a 1-click button when you run the command below).
4. **Expose Local Port 8000 via Tailscale Funnel:**
   ```powershell
   tailscale funnel --bg 8000
   ```
   - Tailscale will print a permanent public URL like:
     `https://desktop-abc123.tail12345.ts.net/`
   - Because we used `--bg`, Tailscale Funnel will **automatically resume in the background** every time you boot your laptop!

---

## Step 3: Set Up Meta WhatsApp Cloud API (RM0 Sandbox + Permanent Token)

### 3.1 Create Your Free Meta Developer App
1. Go to [developers.facebook.com](https://developers.facebook.com/) and log in.
2. Click **My Apps** -> **Create App**.
3. Choose **Other** -> **Next** -> Select **Business** as the app type -> **Next**.
4. Name your app `JARVIS Personal Assistant`, enter your email, and click **Create App**.
5. Scroll down to **WhatsApp** and click **Set up**.

### 3.2 Get Your Phone Number ID & Add Your Personal Number
1. In the left sidebar, go to **WhatsApp** -> **API Setup**.
2. Under **From**, select the free **Test Number** (`+1 555...`).
3. Copy the **Phone number ID** (a long numeric string like `102938475610293`) and paste it into your `.env` file as:
   ```env
   WHATSAPP_PHONE_NUMBER_ID="your_phone_number_id_here"
   ```
4. Under **To**, click **Manage phone number list** and add your personal Malaysian WhatsApp number (`601...`). Meta will send a 6-digit verification code to your WhatsApp to confirm it.
5. Set that exact number (digits only, starting with `60`, no `+` or spaces) in your `.env`:
   ```env
   ALLOWED_WHATSAPP_NUMBER="60123456789"
   ```

### 3.3 Get Your App Secret (For HMAC Webhook Security)
1. In the left sidebar, go to **App settings** -> **Basic**.
2. Click **Show** next to **App secret**, copy it, and paste it into `.env`:
   ```env
   WHATSAPP_APP_SECRET="your_app_secret_here"
   ```

### 3.4 Get Your Access Token (Temporary vs. Permanent)
- **For immediate testing right now:** Copy the **Temporary access token** from **WhatsApp -> API Setup** and put it in `.env` as `WHATSAPP_ACCESS_TOKEN`.
- **To make it never expire (Permanent Free Token):**
  1. Open [Meta Business Settings](https://business.facebook.com/settings/).
  2. Go to **Users** -> **System Users** -> Click **Add** (Name: `JARVIS`, Role: `Admin`).
  3. Click **Assign Assets** -> Select **Apps** -> Select `JARVIS Personal Assistant` -> Enable **Full Control (Manage App)** -> Save.
  4. Click **Generate new token** -> Select your app -> Set Token Expiration to **Never** -> Check permissions `whatsapp_business_messaging` and `whatsapp_business_management` -> Generate and copy into `WHATSAPP_ACCESS_TOKEN` in `.env`.

### 3.5 Connect the Webhook (After Starting JARVIS in Phase 3)
1. Invent any secret phrase and put it in `.env` as `WHATSAPP_VERIFY_TOKEN` (e.g., `jarvis_my_secret_verify_2026`).
2. In Meta Dashboard -> **WhatsApp** -> **Configuration**:
   - **Callback URL:** `https://<your-tailscale-domain>.ts.net/webhook`
   - **Verify token:** The exact `WHATSAPP_VERIFY_TOKEN` from your `.env`.
3. Click **Verify and save**.
4. Under **Webhook fields**, click **Manage** and subscribe to **`messages`**.
