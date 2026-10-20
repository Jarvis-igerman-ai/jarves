# Cloud WhatsApp gateways: GREEN-API and Flow API (flowapi.pro), evaluated for JARVES

**Status:** research only, 21 Oct 2026, Issue #17. I did not sign up for, pay for or link anything.
**Owner's question:** "There is a new API called flowapi; check if it can improve the WhatsApp integration." He then
named GREEN-API (green-api.com) and Flow API (https://flowapi.pro) as "something similar".
**Baseline:** our local whatsmeow linked-device bridge. See `docs/plans/WHATSAPP_REAL_ACCESS.md`,
`bridge/whatsapp/PROTOCOL.md` and `docs/reviews/SWV4_F_WHATSAPP.md` on branch `v4/integrated-b-c-d-g-m-r01`.
**Marking:** **[vendor]** = rests only on the vendor's own pages or marketing. **[checked]** = I verified it
myself today (registry, archive, package index). Claims with no mark come from the vendor's legal or API docs.

## 1. Answer for the owner

**Ignore both for the product.** Each one is the same unofficial "linked device" trick as our bridge, so the
WhatsApp ban risk is the same. The difference is that each one runs your WhatsApp session on **its own servers**.
There it can read, store and send every message on your personal number, and it charges a monthly fee. Our bridge
does the same job on your PC for free, and nobody else holds the session. Flow API also has red flags: **its domain
was registered on 12 Oct 2026 (9 days ago)**, its support address is a Gmail address, and there is no traceable
company record. Do not link your main number to either one.

## 2. What they are

### 2.1 GREEN-API (green-api.com)

- **What:** a commercial **unofficial** WhatsApp HTTP API, operating since 2015 according to its footer. It is
  **not** Meta's Cloud API; it also sells a separate "GREEN-API: WABA" product for the official API.
  - **Linking:** you create an "instance" in their console and get `idInstance` + `apiTokenInstance`. You then scan
    a QR code with your personal phone (Linked devices) or enter a pairing code. From then on the session lives on
    their servers.
- **Capabilities:** send text, media, location, contacts, polls and buttons; receive through webhook or HTTP polling
  (`receiveNotification`); groups; chat history and journals; contacts; read marks; edit and delete; statuses; calls.
- **Python SDK:** `whatsapp-api-client-python` on PyPI **[checked]**. The repo was last pushed 1 Oct 2026, has 209
  stars, and its licence is CC BY-ND 4.0, an unusual choice for code. SDKs also exist for Node, Go, PHP, Java and C++.
- **Pricing:** the free "Developer" plan is limited to **3 chats** (send and receive) and 1 instance, with monthly
  caps per method (getChatHistory 300, checkWhatsapp 100, downloadFile 500). Incoming messages have been limited to
  3 chats since 27 May 2024. That is useless for reading a personal inbox. The paid "Business" plan is priced **per
  instance**: 690 RUB per month per their own 2026 article; third-party listings say about $12 per month. No USD
  price was shown on the docs page.
- **Company and jurisdiction:** «Green-API» LLC, TIN 220940032673, Alau Park, Kabanbai Batyr Ave 47/2, Astana,
  **Kazakhstan**. Kazakh law governs. The privacy policy was last updated 16 Aug 2024; the user agreement was
  published 22 Sep 2022.
- **Hosting:** no server location is stated in the policy. Their own status news names **Yandex.Cloud** (a Russian
  cloud provider) as the host of clusters 5700 and others, in incidents on 11 and 16 Dec 2025. They also report
  outages of 1.5 to 10 hours (Nov 2025 to Jan 2026).
- **Message data:** messages are kept "in the ordinary course of providing our Service", with no retention period
  stated. Personal data is kept up to 6 months after deletion. Data is disclosed to courts and government bodies on
  legal request. The policy says "we offer end-to-end encryption for our Service". **That is misleading for a
  gateway:** the vendor holds the device keys, so it decrypts every message. WhatsApp's end-to-end encryption ends on
  its servers.
- **Security:** the token is part of every request URL (`/waInstance{id}/{method}/{token}`), so it ends up in proxy
  and server logs. Webhook authentication is an optional static `webhookUrlToken` sent in `Authorization`, with
  **no request signing**. Webhooks also need a public HTTPS endpoint on our side.
- **Ban risk, in their own words:** "always carries a risk of losing the option to use WhatsApp". Their own news
  reported that from 10 Aug 2025 blocks rose 20% and logouts 70%, and that logouts ran at 5 times normal on
  3 Dec 2025. The FAQ has a whole "Account blocking" section, with number "warming" and the "yellowCard" status.
  Their user agreement does not mention WhatsApp at all and gives the service "as is".

### 2.2 Flow API (flowapi.pro)

- **What [vendor]:** a "WhatsApp HTTP API for automation tools and AI agents". It states that it is **not** the
  official API and is not affiliated with Meta. Linking works by QR or an 8-character pairing code, as a linked
  device. It is **Green API compatible**: "same method names, request bodies and webhook format". Base URL
  `https://flowapi.pro/waInstance{idInstance}/{method}/{apiTokenInstance}`; the docs tell you to use Green API SDKs
  with the host changed. It also offers an MCP server per number, an n8n node, a Zapier app, Make scenarios, an "AI
  auto-reply" with your own model key, and a 20% affiliate scheme.
- **Capabilities [vendor docs]:** send text, files, location, contacts, polls and buttons; webhooks (3 retries, then
  a polling queue) or polling; groups; chat history; contacts.
- **Pricing [vendor]:** 7-day trial with no card, then **$10 per number per month** or $100 per year. "Unlimited
  messages." There is no free tier beyond the trial.
- **Operator:** the Terms and Privacy Policy (both "Version 2026-10-13") name "DispatchioAI LLC, doing business as
  Flow API". **Israeli law** applies, with the Tel Aviv-Jaffa courts. Support is `elchanan.co@gmail.com`, plus
  `contact@flowapi.pro`. I found **no public registry record** of "DispatchioAI LLC". A DispatchioAI web product
  sells AI voice agents to US field-service firms; whether it is the same company is unverified.
- **Domain age [checked]:** RDAP for flowapi.pro shows registration on **2026-10-12** at Name.com and a change on
  2026-10-17. Archive.org snapshots from Mar and Jul 2025 show an **unrelated Chinese "FLOW.AI relay API" site**
  on the same name. The current product is about one week old: no reviews, no GitHub, no status page, no
  changelog, and no independent reports were found by web search.
- **Data:** "recent messages are kept in your instance journal". Instance data is deleted or anonymised within
  30 days after deletion. Server location is **not stated**; providers "may process data in the US, EU and
  Asia-Pacific". Sub-processors are not named, and Google Ads tags are used. Security is described only as "encryption
  in transit, access controls".
- **Terms:** WhatsApp "may ... permanently ban phone numbers at any time". There is no liability for bans or lost
  chats. Liability is capped at 3 months of fees (or $100). Uptime figures are "goals". Access can be suspended
  without notice.
- **Red flags:** a new domain, a Gmail support address, an untraceable LLC, a Green-API-clone interface, an affiliate
  push, and an "AI auto-reply" on personal numbers. I cannot tell whether it runs its own engine or **resells or
  proxies Green API**. In the proxy case, two unknown parties would hold the session. I found no proof that it is a
  scam. It is simply **unverifiable**, and that alone is disqualifying for a service that would hold the owner's
  WhatsApp keys.

## 3. Comparison

| | Local whatsmeow bridge (ours) | GREEN-API | Flow API (flowapi.pro) |
|---|---|---|---|
| Official? | No (linked device) | No (linked device) | No (linked device) |
| Who holds the session keys | Owner's PC (DPAPI + AES-GCM) | Vendor servers (Kazakhstan company, Yandex.Cloud named in incidents) | Vendor servers, location not stated |
| Who can read all messages | Nobody else; only messages the owner asks about go to OpenAI | Vendor (and lawful requests under Kazakh law) | Vendor (and any upstream provider) |
| Retention | Owner-set 1-90 days or keep-nothing, purge | "Ordinary course"; personal data up to 6 months after deletion | "Recent messages" journal; 30 days after deletion |
| Cost | Free | Free = 3 chats only; Business is about 690 RUB per month per instance | $10 per month per number after a 7-day trial |
| WhatsApp ToS / ban risk | Same | Same, plus a datacenter IP and shared infrastructure **[unverified effect]** | Same, plus unknown |
| Extra risk | Our code to maintain | Vendor outage or breach, token in URL, unsigned webhooks | All of GREEN-API's, plus a 9-day-old unknown operator |
| Inbound network | None (loopback only) | Public webhook, or outbound polling | Public webhook, or outbound polling |
| Policy enforcement (approvals, allow-lists, rate limits, kill) | In the bridge, by design | Must be rebuilt in front of it; the vendor can send without us | Same as GREEN-API |
| Maintenance | We track whatsmeow (weekly commits) | Vendor tracks WhatsApp; 1.5-10 h outages reported | Unknown |
| Open source | whatsmeow MPL-2.0; our bridge in repo | Closed service; SDKs public | Closed; no repo |

**What a gateway would add:** no Go build or engine upkeep on our side, and ready SDKs. **What it costs us:**
privacy (a third party with full read and send power over the personal number), a monthly fee, and a dependency on a
vendor. It cuts no ban risk, because the protocol mechanism is identical. Unlike our bridge, a cloud session can also
be used (by the vendor, an attacker who steals the URL token, or their staff) **outside** the approvals popup.

## 4. Verdict and optional safe trial

- **GREEN-API: ignore for the product.** A trial is acceptable only as an owner-run experiment, under all of the
  conditions below.
- **Flow API: ignore.** Do not trial it until it has a verifiable company, a track record and independent reviews.
- **The local bridge stays the design.** Nothing in package F changes.

If the owner still wants to try GREEN-API (he does it himself; the agent links nothing):

1. Use a **throwaway SIM or number** that has never been his main WhatsApp. **Never the main number.**
2. Use the free Developer plan only (3 chats), with test contacts only, and **read-only**. Do not call `sendMessage`.
3. Use **polling** (`receiveNotification`/`deleteNotification`), never a webhook, so nothing on the PC is
   internet-reachable.
4. Store the token as a secret (DPAPI, the `logs.py` redaction patterns), never in a config file or a log. Delete the
   instance and log the device out at the end.
5. It is **off by default** and never shown in the wizard; it is a developer flag only.

**Code seam, if ever built:** an `engine: "greenapi"` adapter behind the **same BridgeProtocol v1**
(`bridge/whatsapp/PROTOCOL.md`).
- Run it as a local process on 127.0.0.1 with the same tokens, endpoints, `policy.json`, approvals, `KILL` file, rate
  limits and store. It translates:
  - `/chats`, `/messages`, `/search` to `getChats` and `getChatHistory` plus the locally sealed store, fed by polling;
  - `/send` to `sendMessage`, only after the local policy passes (section 4);
  - `/link` to the vendor's QR method;
  - `/unlink` to `logout` plus deleting the instance.
- Section 8 (engine notes) would gain the vendor's state events (`yellowCard`, logout) mapped to the auto-kill.
- The Core, the tools and the fake bridge stay unchanged.
- The limit of this seam: **the bridge's guarantees stop at our process.** The vendor can still read and send
  directly, and that cannot be fixed on our side.

## Appendix: other "FlowAPI" meanings found

- **ServiceNow FlowAPI** (`sn_fd`, Flow Designer server scripting; the owner's first link,
  https://www.servicenow.com/docs/r/api-reference/server-api-reference/ScriptableFlowAPI.html): it runs workflows
  inside a ServiceNow company server. It is not a WhatsApp product, and that link was a false lead.
- **Meta WhatsApp Flows API** (https://developers.facebook.com/documentation/business-messaging/whatsapp/flows/guides/flowsapi,
  updated 16 Jun 2026): in-chat forms for **business** numbers. It does not help a personal account.
- **Other domains:** flowapi.com is parked and for sale. flowapi.io is a parked lander. flowapi.net is "Coming Soon".
  None of them is a WhatsApp product. GREEN-API itself has no "flow" product.

## Sources (checked 21 Oct 2026)

- Flow API: https://flowapi.pro , https://flowapi.pro/docs , https://flowapi.pro/legal/terms , https://flowapi.pro/legal/privacy , https://flowapi.pro/green-api-alternative
- Flow API domain: RDAP https://rdap.org/domain/flowapi.pro ; archive https://web.archive.org/web/20250715030701/http://flowapi.pro/
- DispatchioAI (possibly related, unverified): https://www.dispatchioai.net/
- GREEN-API docs: https://green-api.com/en/docs/ ; plans https://green-api.com/en/docs/about-tariffs/ ; Developer limits news https://green-api.com/en/docs/news/2024/05/20/ ; status news https://green-api.com/en/docs/news/
- GREEN-API price: https://green-api.com/articles/en/how-much-is-whatsapp-business-api-worth-in-2026/ ; Capterra https://www.capterra.com/p/10052094/GREEN-API/
- GREEN-API legal: https://green-api.com/en/privacy-policy , https://green-api.com/en/user-agreement
- GREEN-API webhooks: https://green-api.com/en/docs/api/receiving/technology-webhook-endpoint/
- GREEN-API on bans: https://green-api.com/en/blog/reduce-the-risk-of-WA-blocking/ , https://green-api.com/en/blog/2025/whatsapp-has-become-more-loyal-now-a-warning-instead-of-a-ban/
- Python SDK: https://pypi.org/project/whatsapp-api-client-python/ , https://github.com/green-api/whatsapp-api-client-python
- Competitor claims (marketing; not used for the verdict): https://whatsloop.net/en/blog/green-api-alternatives-whatsapp-business
