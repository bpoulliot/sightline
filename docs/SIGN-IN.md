# Sign-in mode: the reference deployment

Sightline is a single-user local tool by default (`docker compose up`). This runbook is for the other
case: one instance offered to several people, each signing in with the institution's own sign-on.
It is roadmap item 47; the decisions behind it are item 40's, and the app's side of it is items 44
to 46 (README, *Sign-in behind a proxy*).

**Nothing here has been deployed by this project.** The pilot runs on the operator's own host,
behind a TLS terminator and an Authentik already running there (ROADMAP *Deployment*); its first
live runs follow [PILOT.md](PILOT.md), which starts from this runbook. The compose file and both worked examples are checked offline — the compose file by
`tests/test_signin_compose.py` and `docker compose config`, the oauth2-proxy configuration by
starting that proxy with no network — and no identity provider was contacted to write this. Every
host name, id and secret below is fake: `canvas.example.edu`, `auth.example.edu`,
`sightline.example.edu`, all-zero ids. Replace each one with your own, in files that are not
committed.

- [The shape](#the-shape)
- [Authentik, the reference](#authentik-the-reference)
- [The master key](#the-master-key)
- [Checks after it is up](#checks-after-it-is-up)
- [A second worked example: oauth2-proxy in front of Microsoft Entra ID](#a-second-worked-example-oauth2-proxy-in-front-of-microsoft-entra-id)
- [Rotating the shared secret](#rotating-the-shared-secret)

## The shape

Three parts, one process each: the **identity provider** (Authentik), which federates the
institution's sign-on; the **proxy** (an Authentik proxy outpost), which holds each person's session
and forwards their requests with who they are; and **the app**. `docker-compose.signin.yml` runs the
last two and attaches to an Authentik that already exists.

```
browser ──https──▶ your TLS terminator ──▶ 127.0.0.1:9000 ─▶ authentik-outpost ──(proxy, internal)──▶ sightline:8000
                                                                    │                                     │
                                                       (edge) ──────┘ to Authentik          (egress) ─────┘ to the model host and Canvas
```

- **The app publishes no port.** It shares the `proxy` network with the outpost alone, and that
  network is `internal`: no route out, nothing published. Nothing else can open a connection to it.
- **The app trusts identity only from the proxy.** The outpost adds a shared secret header to every
  request it forwards; the app refuses any request without it, with 401, before any route runs
  (item 44). The network is the first wall and the secret the second.
- **The app's egress network keeps the pinned subnet** (`SIGHTLINE_SUBNET`, same default as the
  local compose file; ROADMAP *Deployment* says why). Running this file and `docker-compose.yml` on
  one host at once needs `SIGHTLINE_SUBNET` overridden for one of them. The outpost's `edge` network
  and the `proxy` network are pinned too (`SIGHTLINE_EDGE_SUBNET`, `SIGHTLINE_PROXY_SUBNET`).
- **The outpost listens on plain HTTP, on the loopback** (`127.0.0.1:9000`). Put the TLS terminator
  you already run in front of it; the session cookie must travel over HTTPS. `SIGHTLINE_SIGNIN_BIND`
  and `SIGHTLINE_SIGNIN_PORT` change where it listens.
- **Its data is its own.** The project is named `sightline-signin`, so its volume is
  `sightline-signin_sightline-data`, separate from a local-mode instance's.

## Authentik, the reference

### Where no Authentik exists

Run one first, by Authentik's own instructions: [Docker Compose
installation](https://docs.goauthentik.io/install-config/install/docker-compose/). Sightline does
not ship a copy of that compose file; it changes with every Authentik release. Note the version you
install: the outpost's must match it.

### 1. Federate the institution's sign-on

In Authentik, under **Directory → Federation and social login**, add a source for the institution's
identity provider — a SAML source, or an OAuth/OpenID Connect source (Authentik has a source type
for Entra ID). Authentik's [sources documentation](https://docs.goauthentik.io/users-sources/sources/)
covers each. Add the source to the authentication flow that people will sign in through, so they
are sent to the institution's sign-on rather than asked for an Authentik password.

### 2. The two groups

Create two groups, for example `sightline-users` and `admins`: the people who may use Sightline,
and the people who may also change instance settings. Make people members by hand, or by mapping a
group from the source if yours carries groups. Authentik's outpost sends group **names**, separated
by `|`, so the app's settings are the names:

```
SIGHTLINE_SIGNIN_USER_GROUP=sightline-users
SIGHTLINE_SIGNIN_ADMIN_GROUP=admins
```

An admin sees every person's runs and alone changes the Canvas base URL, the export preference,
the model host and the model (item 45). With the admin setting unset nobody is an admin.

Who may use Sightline at all is checked twice (item 51). **Authentik**: bind *both* groups to the
application in step 5, so nobody else is let through to the app. Membership of either group
passes the binding policy, so an admin does not also need to be in the users group. **The app**:
with `SIGHTLINE_SIGNIN_USER_GROUP` set, a signed-in person in neither group gets a 403 page naming
the users group to ask for, and nothing else, so a binding loosened later does not open the app to
everyone Authentik knows. Group names are matched exactly. Unset, the app admits anyone the outpost
forwards and logs `signin_any_identity_admitted` at startup; Authentik's binding is then the only
gate. Taking a person out of the groups deletes nothing: their runs stay, and an admin still sees
them.

### 3. An 8-hour session that ends when the browser closes

Two settings, both Authentik's (the app holds no session of its own):

- **When the browser closes** — the **User Login** stage of the authentication flow: **Session
  duration** `seconds=0` ("until the browser session ends") and **Remember me offset** `seconds=0`
  (hides *Remember me on this device*). This stage is shared by every application signing in through
  that flow. If other applications need something else, copy the flow for Sightline and select the
  copy on the provider.
- **8 hours** — the proxy provider's **Token validity** (step 5): `hours=8`.

Check both by hand once it is up ([Checks](#checks-after-it-is-up)). Browsers that restore a
session on restart can keep a session cookie alive past a close; that is the browser's behaviour,
and Authentik's documentation says as much. Whether the outpost's own cookie is a session cookie
in your Authentik version was not checked here: if the check after closing the browser still gets
straight in, the 8-hour bound still holds, and that is the setting to rely on.

### 4. The shared secret

Generate it once:

```
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

The app reads it from `SIGHTLINE_SIGNIN_SECRET` (at least 32 characters) and expects it in the
header `X-Sightline-Proxy-Secret`. Authentik adds it with a **scope mapping**: **Customization →
Property Mappings → Create → Scope Mapping**, named for example `Sightline proxy secret`, with any
scope name (`sightline_proxy_secret`), and this expression, the secret pasted in place of the fake
value:

```python
return {
    "ak_proxy": {
        "user_attributes": {
            "additionalHeaders": {
                "X-Sightline-Proxy-Secret": "fake-signin-secret-0123456789-abcdefghij",
            }
        }
    }
}
```

The value is then stored in Authentik's database, readable by whoever administers Authentik. Use a
scope mapping, not a group's or a user's `additionalHeaders` attribute: an attribute is visible to
anyone who can read that group or user, and a mapping takes precedence if both are set. The
expression is evaluated when a person signs in, so a changed secret reaches the outpost at each
person's next sign-in ([Rotating](#rotating-the-shared-secret)).

### 5. The provider and the application

**Applications → Providers → Create → Proxy Provider**:

| Setting | Value |
|---|---|
| Mode | **Proxy** (not *Forward auth*: the outpost is the only way to the app) |
| External host | `https://sightline.example.edu` — the address people type |
| Internal host | `http://sightline:8000` — the app on the `proxy` network |
| Token validity | `hours=8` |
| Advanced protocol settings → Additional scopes | the mapping from step 4 |
| Authentication flow | the flow from steps 1 and 3 |

**Applications → Applications → Create**: a name, slug `sightline`, and the provider above. Then, on
the application's **Policy / Group / User Bindings** tab, bind the group `sightline-users` and the
group `admins` (step 2). With the default policy engine mode, *any*, a member of either passes.

### 6. The outpost

**Applications → Outposts → Create**: type **Proxy**, integration **none** (it is deployed by hand,
by this compose file), and select the `sightline` application. Then **View deployment info** shows
the token the outpost authenticates with.

### 7. The host

Two environment files beside `docker-compose.signin.yml`, both gitignored by `.env.*` and kept out
of the image by `.dockerignore`. The app never holds the outpost's token, and the outpost never
holds a Canvas token.

`.env` — the app, as `.env.example` describes, plus:

```
SIGHTLINE_SIGNIN_SECRET=fake-signin-secret-0123456789-abcdefghij
SIGHTLINE_SECRET_KEY=fake-master-key-generate-your-own-see-below
SIGHTLINE_SIGNIN_USER_GROUP=sightline-users
SIGHTLINE_SIGNIN_ADMIN_GROUP=admins
CANVAS_BASE_URL=https://canvas.example.edu
AUTHENTIK_TAG=2099.1.0
```

`SIGHTLINE_SIGNIN=true` is set by the compose file. The header names need no setting: the app's
defaults are the outpost's (`X-authentik-uid` for the subject, `X-authentik-name`,
`X-authentik-groups` separated by `|`, and `X-Sightline-Proxy-Secret`). `AUTHENTIK_TAG` is your
Authentik server's version; compose reads it from `.env` when it interpolates the file and refuses
to start without it. `SIGHTLINE_SECRET_KEY` is the master key ([below](#the-master-key)); compose
and the app both refuse to start without it.

`.env.outpost` — the outpost alone:

```
AUTHENTIK_HOST=https://auth.example.edu
AUTHENTIK_TOKEN=fake-outpost-token
AUTHENTIK_INSECURE=false
```

`AUTHENTIK_HOST` must be reachable from the outpost's container. If people reach Authentik at a
different address than the outpost does, add `AUTHENTIK_HOST_BROWSER` with the public one.

Then point the TLS terminator for `sightline.example.edu` at `127.0.0.1:9000`, and:

```
docker compose -f docker-compose.signin.yml config -q    # renders, or names what is missing
docker compose -f docker-compose.signin.yml up -d --build
```

## The master key

Each person's Canvas token is encrypted at rest under one key, `SIGHTLINE_SECRET_KEY` (item 45
built the encryption; item 50 made the key required here). In sign-in mode it comes from the host's
secret environment, never from a file in the data volume, and the app refuses to start without a
valid one, naming the variable. Generate it once:

```
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Keep it in the host's secret store, and back it up there, apart from the data:
`python -m sightline.backup` deliberately leaves the key out (README, *Backing up*). **Changing it
means every person enters their Canvas token again**: the stored tokens do not decrypt under
another key, and there is no rotation command. A per-person passphrase was considered and
declined, because a queued or resumed run must reach Canvas while its starter is away.

An instance that ran sign-in mode before item 50 kept its key in `<data root>/secrets/credentials.key`.
To keep its tokens, set `SIGHTLINE_SECRET_KEY` to that file's contents; otherwise every person
enters their token again.

## Checks after it is up

Run these once, by hand, after every change to the provider or the flow:

1. **The app is unreachable except through the outpost.** From the host,
   `docker compose -f docker-compose.signin.yml ps` lists a published port for the outpost and none
   for `sightline`.
2. **A signed-out browser is sent to the institution's sign-on**, and back to Sightline after.
3. **Settings reads *You are signed in as …*** with your name, and shows the instance settings'
   forms only to a member of the admin group.
4. **One person in each group signs in; one person in neither is refused.** A member of the users
   group and a member of the admin group each reach Sightline. A person in neither is stopped by
   the identity provider's binding; if they reach the app at all, it answers with a page naming
   the users group.
5. **Closing the browser ends the session**: reopen it and Sightline asks you to sign in.
6. **Eight hours later it asks again**, browser open or not.

If 3 shows no name, or the app answers every request with *Sign-in required*, the outpost is not
adding the secret (step 4 not in the provider's Additional scopes, or the person signed in before it
was) or the secret differs between Authentik and `.env`. The app's log records which check refused
a request; the response never says.

## A second worked example: oauth2-proxy in front of Microsoft Entra ID

Nothing in the app is Authentik's: the header names, their separator and the two groups are
configuration. This example replaces the outpost with [oauth2-proxy](https://oauth2-proxy.github.io/oauth2-proxy/)
signing people in to Entra ID directly. It was written against oauth2-proxy **v7.13.0**: its
configuration below parses, and the proxy starts with it and with discovery stubbed, offline.

### Entra ID's trap: the group claim carries object ids, not names

Entra puts a group's **object id** in the token, for example
`00000000-0000-0000-0000-0000000000a1`, never its display name. oauth2-proxy forwards what the
token carries. So both groups are configured as those ids:

```
SIGHTLINE_SIGNIN_USER_GROUP=00000000-0000-0000-0000-0000000000b2
SIGHTLINE_SIGNIN_ADMIN_GROUP=00000000-0000-0000-0000-0000000000a1
```

Setting the admin group to its display name matches nobody, silently: every person is signed in and
nobody is an admin. Setting the users group to its display name refuses everybody who is not an
admin. Look the id up on the group's overview page in the Entra admin center.

A person in more than 200 groups gets an *overage* claim instead of the list; oauth2-proxy then
asks Microsoft Graph for the groups, which needs the `User.Read` scope below. Without it such a
person signs in with no groups.

### The app registration

In Entra ID, **App registrations → New registration**:

- **Redirect URI** (Web): `https://sightline.example.edu/oauth2/callback`.
- **Certificates & secrets**: a client secret. It goes in a file, below, never in a compose file.
- **Token configuration → Add groups claim**: *Security groups*, emitted in the ID token as
  *Group ID*.
- Note the **Application (client) id** and the **Directory (tenant) id**.

### The proxy's configuration

Header injection that adds a static secret needs oauth2-proxy's *alpha configuration* file, which
replaces `--pass-user-headers` and the provider flags; the cookie settings stay environment
variables. Keep these files out of the repository, under the gitignored `private/`:

`private/oauth2-proxy/oauth2-proxy.yaml`:

```yaml
server:
  bindAddress: "0.0.0.0:4180"
upstreamConfig:
  upstreams:
    - id: sightline
      path: /
      uri: http://sightline:8000
providers:
  - id: entra
    provider: entra-id
    clientID: 00000000-0000-0000-0000-000000000000
    clientSecretFile: /etc/oauth2-proxy/client-secret
    scope: openid profile email User.Read
    allowedGroups:
      - 00000000-0000-0000-0000-0000000000b2
      - 00000000-0000-0000-0000-0000000000a1
    oidcConfig:
      issuerURL: https://login.microsoftonline.com/00000000-0000-0000-0000-000000000000/v2.0
      userIDClaim: oid
      groupsClaim: groups
      emailClaim: email
      audienceClaims: [aud]
injectRequestHeaders:
  - name: X-Forwarded-User
    values:
      - claim: user
  - name: X-Forwarded-Preferred-Username
    values:
      - claim: preferred_username
  - name: X-Forwarded-Groups
    values:
      - claim: groups
  - name: X-Sightline-Proxy-Secret
    values:
      - fromEnv: SIGHTLINE_SIGNIN_SECRET
```

- `userIDClaim: oid` makes the subject the person's Entra object id, which is stable. The default
  is their email address, which changes when they are renamed — and the subject is what keeps a
  renamed person's history (item 40, decision 1).
- Each injected header **replaces** whatever the client sent under that name, and several groups
  arrive as one header joined by `,`. Both are oauth2-proxy's behaviour as of v7.13.0; the app
  refuses a header that arrives twice either way.
- `allowedGroups` is the proxy's half of item 51's check: it lets through only a person in one of
  the listed groups (object ids, the users group and the admin group), and refuses anyone else
  before the request reaches the app. The app's `SIGHTLINE_SIGNIN_USER_GROUP` is the other half.
- `fromEnv` reads the shared secret from the proxy's own environment.
- The header values in oauth2-proxy's documentation are shown nested under `claimSource:` and
  `secretSource:`; v7.13.0 refuses that (`unknown field "claimSource"`) and takes the flat form
  above.

`private/oauth2-proxy/client-secret`: the client secret, alone on one line.

`.env.oauth2-proxy`, beside the compose file:

```
SIGHTLINE_SIGNIN_SECRET=fake-signin-secret-0123456789-abcdefghij
OAUTH2_PROXY_COOKIE_SECRET=fake-cookie-secret-32-bytes-long
OAUTH2_PROXY_COOKIE_EXPIRE=8h
OAUTH2_PROXY_REDIRECT_URL=https://sightline.example.edu/oauth2/callback
OAUTH2_PROXY_EMAIL_DOMAINS=*
OAUTH2_PROXY_REVERSE_PROXY=true
```

The cookie secret is 32 random bytes: `python -c "import secrets; print(secrets.token_urlsafe(24))"`.
`OAUTH2_PROXY_EMAIL_DOMAINS=*` lets anyone the tenant signs in through as far as the email domain
goes; `allowedGroups` above is what limits it to the two groups.

**The session is one or the other, not both.** oauth2-proxy's `--cookie-expire` is either a
duration — `8h`, a cookie that lasts 8 hours and survives a closed browser — or `0`, a session
cookie that ends when the browser closes and has no age limit. It has no setting for both. The
example takes `8h`; choose `0` if closing the browser matters more, or use Entra's own sign-in
frequency controls for the other half.

### The compose service

In a copy of `docker-compose.signin.yml`, replace the `authentik-outpost` service with:

```yaml
  oauth2-proxy:
    image: quay.io/oauth2-proxy/oauth2-proxy:v7.13.0
    command: ["--alpha-config", "/etc/oauth2-proxy/oauth2-proxy.yaml"]
    restart: unless-stopped
    env_file:
      - path: .env.oauth2-proxy
        required: true
    volumes:
      - ./private/oauth2-proxy:/etc/oauth2-proxy:ro
    ports:
      - "127.0.0.1:4180:4180"
    networks:
      - proxy
      - edge
    depends_on:
      - sightline
```

and set the app's header names in `.env`:

```
SIGHTLINE_SIGNIN_SUBJECT_HEADER=X-Forwarded-User
SIGHTLINE_SIGNIN_NAME_HEADER=X-Forwarded-Preferred-Username
SIGHTLINE_SIGNIN_GROUPS_HEADER=X-Forwarded-Groups
SIGHTLINE_SIGNIN_GROUPS_SEPARATOR=,
SIGHTLINE_SIGNIN_USER_GROUP=00000000-0000-0000-0000-0000000000b2
SIGHTLINE_SIGNIN_ADMIN_GROUP=00000000-0000-0000-0000-0000000000a1
```

The secret header keeps its default name, `X-Sightline-Proxy-Secret`. Point the TLS terminator at
`127.0.0.1:4180` and run the same [checks](#checks-after-it-is-up).

## Rotating the shared secret

Generate a new one, then change it in both places and restart the app:

1. the proxy's copy — Authentik's scope mapping (step 4), or `.env.oauth2-proxy`;
2. `SIGHTLINE_SIGNIN_SECRET` in `.env`, then
   `docker compose -f docker-compose.signin.yml up -d sightline`.

Between the two, requests are refused. With Authentik the mapping is evaluated at sign-in, so a
person already signed in keeps sending the old secret until they sign in again: plan the rotation
for when sessions have ended, or expect people to be asked to sign in once more.

The master key is a different thing and is not rotated: a new `SIGHTLINE_SECRET_KEY` means every
person enters their Canvas token again ([The master key](#the-master-key)).
