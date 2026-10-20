# "FlowAPI" and the JARVES WhatsApp integration (evaluation)

**Status:** research only, 21 Oct 2026, Issue #17. Nothing signed up for, paid for or linked.
**Question from the owner:** "There is a new API called flowapi. Check if it can improve the WhatsApp integration."
**Link from the owner:** https://www.servicenow.com/docs/r/api-reference/server-api-reference/ScriptableFlowAPI.html
**Compared with:** the chosen design, a local whatsmeow linked-device bridge
(`docs/plans/WHATSAPP_REAL_ACCESS.md`, `bridge/whatsapp/PROTOCOL.md`, `docs/reviews/SWV4_F_WHATSAPP.md` on branch
`v4/integrated-b-c-d-g-m-r01`).

## 1. Answer for the owner

**Not relevant.** ServiceNow's FlowAPI runs automation workflows inside a paid ServiceNow company server. It has
nothing to do with personal WhatsApp. ServiceNow's own WhatsApp connectors only work with a **business** number.
They would also send every message through ServiceNow and Meta or Twilio. Keep the local bridge.

## 2. What the linked page is (checked 21 Oct 2026)

- The page documents the **`FlowAPI`** class in the `sn_fd` namespace (Flow Designer). The URL says
  "ScriptableFlowAPI", but the page heading is FlowAPI. It is labelled release "Brazil", updated 10 Sep 2026.
- It is a **server-side JavaScript API that runs inside a ServiceNow instance**. It starts Flow Designer flows,
  subflows and actions, synchronously or asynchronously: `startFlow`, `executeFlow`, `executeSubflow`,
  `executeAction`, the `*Quick` variants, `getOutputs`, `getStatus`, `cancel`, `restartFlowFromContext`. Many
  methods are now replaced by `ScriptableFlowRunner`, returned by `getRunner()`.
- Its `sendMessage()` only passes a message to a paused flow that waits on a "Wait for Message" step. It is internal
  to the workflow and is **not** a chat or messaging channel. The page never mentions WhatsApp.
- **What it needs:** a ServiceNow instance (enterprise SaaS, licensed per customer), a user allowed to run scripts
  there, and the ids of flows built in Flow Designer. It cannot be called from a PC. A JARVES Core would have to call
  the instance over REST, which then runs the script.
- It is not new. Flow Designer's FlowAPI has existed for several ServiceNow releases; only the doc page is recent.

## 3. Can ServiceNow help with WhatsApp at all?

ServiceNow has WhatsApp channels for its Virtual Agent and customer-service (CSM) products:

| Route | What it connects | Source |
|---|---|---|
| "Conversational Integration with WhatsApp" (Store app), direct to Meta Cloud API | A **WhatsApp Business** number: Meta app, App Secret, phone number id, system user token. Meta's webhook calls the instance at `/api/sn_va_whatsapp/va_whatsapp_adapter`. Needs Integration Hub and an entitlement. | ServiceNow docs on GitHub, Community thread, eesel.ai (third party) |
| "Conversational Integration with WhatsApp (powered by Twilio)" | A Twilio WhatsApp sender (a business number); "a Twilio subscription is required" | eesel.ai, Community article |
| Infobip omnichannel app | Business number through Infobip | eesel.ai |

For the owner's needs, each of these is a step back from the local bridge:

- **Wrong account type.** All of them run on the WhatsApp Business Platform. That platform cannot run a personal
  Messenger account. The number must be deleted from WhatsApp or moved to the Business app in "coexistence", and
  then groups do not sync. Free text is allowed only within 24 h of the contact's last message, and everything else
  needs a pre-approved template. See section 2.1 of WHATSAPP_REAL_ACCESS.md. The owner could not read his normal
  chats or freely text a friend.
- **Privacy.** Every incoming and outgoing message would pass through Meta's Cloud API (or Twilio or Infobip)
  **and** be stored in a ServiceNow instance, a US-based cloud company. Today the bridge keeps messages on the PC in
  a DPAPI-sealed store, and only the messages the owner asks about reach OpenAI. That would mean two more parties
  that see all his messages.
- **Cost.** ServiceNow (Integration Hub, Virtual Agent or CSM licences) is priced for enterprises; I found no public
  list price. Meta charges per message since 1 Oct 2026, and the Twilio route adds a subscription. The owner wants
  no recurring fees.
- **The free developer instance does not solve it.** A Personal Developer Instance (PDI) is free, but ServiceNow's
  own guide says PDIs are "NOT intended to be used for business or production purposes". They are reclaimed after
  10 days of inactivity, and "many ServiceNow Store applications cannot be installed to PDIs". Plugins that need
  ServiceNow staff to activate them are not offered. Late July 2026 forum posts report PDIs stuck in hibernation and
  provisioning paused. Whether the WhatsApp Store app installs on a PDI at all is **unverified**, and the business
  number would still be needed.
- **Complexity.** It needs a public webhook into a cloud instance, Meta app secrets, a system user token, and flows to
  build and maintain. This contradicts the rule in MULTI_DEVICE_REMOTE.md that nothing is internet-reachable. The local
  bridge has none of this.
- **Ban risk.** The official API carries no ToS ban risk, which is the one real advantage. It only applies to a
  business number, though, not to the owner's personal account.

**Could it complement the bridge?** Only if the owner ever wants a separate, public "JARVES business number" for
people to message, and also already runs ServiceNow. Neither is true today. Even then, the plain Meta Cloud API would
be simpler than going through ServiceNow.

## 4. Recommendation

**Ignore.** There is no trial worth running. Nothing changes in the WhatsApp plan or in package F.

## Appendix: other "FlowAPI" candidates found (21 Oct 2026)

- **Meta WhatsApp Flows API**
  (https://developers.facebook.com/documentation/business-messaging/whatsapp/flows/guides/flowsapi, updated
  16 Jun 2026): a Graph API for creating and publishing in-chat forms that **businesses** send. It needs a WABA and a
  business token. It has no use for a personal account.
- **Domains:** flowapi.com is a parked domain for sale on GoDaddy. flowapi.io redirects to a parked "/lander"
  page. flowapi.net is a "FlowAPI — Coming Soon" placeholder, and flowapi.cloud redirects to it. getflowapi.com
  shows only a bot-check page. flowapi.dev returns 404. No WhatsApp gateway called FlowAPI was found by web search.
- Turn.io and Twilio offer hosted "WhatsApp Flows" endpoints. These are business products, and they would see all
  messages.

## Sources (all fetched or searched 21 Oct 2026)

- ServiceNow FlowAPI page (owner's link): https://www.servicenow.com/docs/r/api-reference/server-api-reference/ScriptableFlowAPI.html
- ServiceNow WhatsApp channel docs: https://github.com/ServiceNow/ServiceNowDocs/blob/australia/markdown/conversational-interfaces/virtual-agent/messg-whatsapp-using.md
- Community, VA with WhatsApp Cloud API: https://www.servicenow.com/community/virtual-agent-forum/virtual-agent-in-whatsapp-with-whatsapp-cloud-api/td-p/3608796
- Community, CSM messaging integrations: https://www.servicenow.com/community/csm-articles/messaging-integrations-in-csm-omnichannel/ta-p/3456020
- eesel.ai overview (third-party vendor blog; the table of routes and requirements rests on it): https://www.eesel.ai/blog/meta-muse-for-servicenow
- PDI guide: https://developer.servicenow.com/print_page.do?release=australia&category=developer-program&identifier=accessing-your-pdi&module=guide
- PDI outage reports (forum, unverified): https://www.servicenow.com/community/servicenow-ide-sdk-and-fluent/pdi-stuck-in-quot-temporarily-hibernating-due-to-an/td-p/3579102
- Meta Flows API: https://developers.facebook.com/documentation/business-messaging/whatsapp/flows/guides/flowsapi
- Twilio WhatsApp Flows: https://www.twilio.com/docs/content/whatsapp-flows ; Turn.io: https://whatsapp.turn.io/docs/api/whatsapp_flows
