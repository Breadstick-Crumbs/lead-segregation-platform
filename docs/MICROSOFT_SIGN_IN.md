# Sign in with Microsoft

Users sign in with their Microsoft 365 / Entra ID work account. The login is single-tenant:
only accounts from the configured directory can get in.

## How it works

1. **Start.** `GET /api/v1/auth/microsoft/login` redirects the browser to Microsoft with
   `state`, `nonce` and a PKCE `S256` challenge. Those values, plus the PKCE verifier, are
   kept in a signed, httpOnly, 10-minute `entra_state` cookie (`SameSite=Lax`, because
   Microsoft's redirect back is a cross-site navigation).
2. **Callback.** Microsoft redirects to `/api/v1/auth/microsoft/callback?code&state`. The
   backend then:
   - checks the state against the cookie;
   - redeems the code with the client secret and the PKCE verifier;
   - verifies the ID token: RS256 signature against the tenant's JWKS (cached, refetched
     when a key rotates), audience, issuer, tenant, expiry and nonce.
3. **Account matching.** There is no self-signup:
   - An existing link to the Entra **object ID** wins. It is stable even if the email
     changes.
   - Otherwise the backend matches the user's UPN (`preferred_username`) to an account an
     admin created, and links it.
   - An account already linked to a *different* Microsoft account is refused, never
     re-linked.
   - Inactive accounts are refused.
4. **Session.** On success the backend sets the usual refresh cookie and redirects to
   `<FRONTEND_URL>/auth/callback`; the dashboard then calls `POST /auth/refresh`. No token
   ever appears in a URL.
5. **Failure.** The browser is redirected to
   `<FRONTEND_URL>/login?sso_error=<cancelled|not_invited|inactive|link_mismatch|failed>`.
   Details go to the logs only.

Accounts created without a password are Microsoft-only. `GET /api/v1/auth/providers` tells
the login screen whether to show the Microsoft button.

## Setup (Microsoft Entra admin center)

1. Go to **Entra ID → App registrations → New registration**.
   - **Name:** Lead Segregation Platform
   - **Supported account types:** *Accounts in this organizational directory only (single
     tenant)*
   - **Redirect URI:** platform *Web*,
     `http://localhost:5173/api/v1/auth/microsoft/callback`
2. Under **Authentication**, add the production redirect URI:
   `https://leads.bricbybric.ae/api/v1/auth/microsoft/callback`.
3. Under **Certificates & secrets → New client secret**, copy the secret's **Value**. It is
   shown once.
4. From **Overview**, copy the **Application (client) ID** and the **Directory (tenant)
   ID**. Use the tenant ID, not the domain name.
5. **API permissions:** the default delegated `User.Read`, plus `openid`, `profile` and
   `email`, is all the app needs. No admin consent is required.
6. Set these in `backend/.env`:

   ```
   ENTRA_TENANT_ID=<directory (tenant) id>
   ENTRA_CLIENT_ID=<application (client) id>
   ENTRA_CLIENT_SECRET=<secret value>
   FRONTEND_URL=http://localhost:5173
   ```

7. Create the user in the dashboard (or `POST /api/v1/users`) with their work email, and
   omit the password for a Microsoft-only account.

**Testing without a company tenant:** any Entra directory works, including the default one
created with a free Azure account. Add a test user there and invite them as described above.

Client secrets expire (24 months at most). Note the expiry date and rotate the secret before
then.
